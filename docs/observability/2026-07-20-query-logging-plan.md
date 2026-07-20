# Query logging — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** кожне змістовне питання до Telegram-бота лишає рядок у прод-БД (хто / що / що відповіли /
скільки чекав / коли), який видно однією командою `deploy/psql.sh --queries`.

**Architecture:** таблиця `query_logs` + `QueryLogRepository` (Protocol → Postgres-реалізація → фейк).
Запис робить `handle_question` в обох гілках — після успіху й у `except`. Виклик загорнутий у
`try/except` **на місці виклику**: збій моніторингу не має права зламати відповідь юзеру. Доменної
моделі нема — репозиторій бере типізовані параметри (обґрунтування в дизайні).

**Tech Stack:** SQLAlchemy async + Alembic, aiogram (kwargs-інжекція через `Dispatcher`), pytest
(`asyncio_mode=auto`, фейки без Docker), bash + psql.

**Дизайн:** [`2026-07-20-query-logging-design.md`](2026-07-20-query-logging-design.md) — рішення й
відхилене читати там, тут їх не переповідаю.

**Гілка:** робота йде на `feat/query-logging`, зводиться в `main` після Task 6.

---

## Структура файлів

| Файл | Відповідальність | Дія |
|---|---|---|
| `src/prophet_checker/models/db.py` | ORM-рядок `QueryLogDB` | Modify |
| `alembic/versions/<hash>_add_query_logs.py` | міграція таблиці | Create |
| `src/prophet_checker/storage/interfaces.py` | Protocol `QueryLogRepository` | Modify |
| `src/prophet_checker/storage/postgres.py` | `PostgresQueryLogRepository` | Modify |
| `tests/fakes.py` | `FakeQueryLogRepo` (+ режим «падати») | Modify |
| `src/prophet_checker/bot/handlers.py` | запис у обох гілках, ізольований від відповіді | Modify |
| `src/prophet_checker/bot/runner.py` | репо в `Dispatcher` kwargs | Modify |
| `src/prophet_checker/factory.py` | побудова репо всередині `build_bot` | Modify |
| `tests/test_bot_handlers.py` | поведінка запису | Modify |
| `tests/test_bot_runner.py` | інжекція репо | Modify |
| `deploy/psql.sh` | зріз `--queries` | Modify |
| `runbook/bot.md`, `progress.md`, `docs/README.md` | документація | Modify |

`app.py` **не змінюється**: репозиторій будується всередині `build_bot`, після guard-а `bot_enabled`,
тож при вимкненому боті зайвий engine не створюється.

---

## Task 0: Гілка

**Скоуп:** ізолювати роботу від `main`.

- [ ] **Крок 1: створити гілку**

```bash
cd /Users/evgenijberlog/Brain/prediction-tracker
git checkout -b feat/query-logging
```

- [ ] **Крок 2: зафіксувати базлайн тестів**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: усе зелене. Запиши число тестів — воно знадобиться в Task 6.

---

## Task 1: Таблиця `query_logs`

**Скоуп:** ORM-модель і міграція. Тут нема поведінки, тому нема юніт-тесту — CLAUDE.md прямо
забороняє тестувати чисті Pydantic/ORM-декларації. Замість тесту гейт — міграція, що застосовується
й відкочується на реальному Postgres.

**Files:**
- Modify: `src/prophet_checker/models/db.py`
- Create: `alembic/versions/<hash>_add_query_logs.py`

- [ ] **Крок 1: підняти локальний Postgres**

```bash
docker compose up -d
.venv/bin/alembic upgrade head
```

Expected: контейнер `prophet_postgres` healthy, `alembic upgrade` завершується без помилок.

- [ ] **Крок 2: додати `BigInteger` в імпорти `models/db.py`**

У наявному блоці `from sqlalchemy import (...)` (рядки 7–19) додай `BigInteger` **першим** елементом —
список відсортований за алфавітом:

```python
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
```

- [ ] **Крок 3: додати модель у кінець `models/db.py`**

```python
class QueryLogDB(Base):
    """Слід публічного запиту до бота. Пишеться, читається лише через psql."""

    __tablename__ = "query_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # BigInteger, не Integer: Telegram user id виходить за межі int32
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)  # NULL = впало до відповіді
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_query_logs_created_at", "created_at"),)
```

- [ ] **Крок 4: згенерувати міграцію**

```bash
.venv/bin/alembic revision --autogenerate -m "add query_logs"
```

Expected: створено файл у `alembic/versions/`. **Відкрий його і звір** — `upgrade()` має містити
`op.create_table("query_logs", ...)` з шістьма колонками та `op.create_index("ix_query_logs_created_at", ...)`,
`downgrade()` — дзеркальні `drop_index` + `drop_table`. Якщо autogenerate притягнув щось стороннє
(діф по інших таблицях), **видали зайве руками** — міграція має містити тільки `query_logs`.

- [ ] **Крок 5: застосувати й відкотити**

```bash
.venv/bin/alembic upgrade head
docker compose exec -T postgres psql -U prophet -d prophet -c '\d query_logs'
.venv/bin/alembic downgrade -1
.venv/bin/alembic upgrade head
```

Expected: `\d query_logs` друкує шість колонок (`user_id` типу `bigint`, `answer` nullable) та індекс
`ix_query_logs_created_at`. `downgrade` проходить без помилок — це доводить, що міграція оборотна.
Якщо ім'я користувача/БД інше, візьми його з `docker-compose.yml`.

- [ ] **Крок 6: коміт**

```bash
git add src/prophet_checker/models/db.py alembic/versions/
git commit -m "feat(storage): таблиця query_logs для сліду запитів бота"
```

---

## Task 2: Репозиторій — Protocol, Postgres, фейк

**Скоуп:** шар доступу. Postgres-реалізацію цей набір тестів не покриває (сюїта герметична, без
Docker) — її гейт це смоук у Task 6. Фейк тут не тестується сам по собі, він інструмент для Task 3.

**Files:**
- Modify: `src/prophet_checker/storage/interfaces.py`
- Modify: `src/prophet_checker/storage/postgres.py`
- Modify: `tests/fakes.py`

- [ ] **Крок 1: Protocol у кінець `storage/interfaces.py`**

```python
class QueryLogRepository(Protocol):
    async def save(
        self, user_id: int, question: str, answer: str | None, latency_ms: int
    ) -> None: ...
```

- [ ] **Крок 2: реалізація в кінець `storage/postgres.py`**

Спершу додай `QueryLogDB` до наявного імпорту з `prophet_checker.models.db`, потім клас:

```python
class PostgresQueryLogRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def save(
        self, user_id: int, question: str, answer: str | None, latency_ms: int
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                QueryLogDB(
                    user_id=user_id,
                    question=question,
                    answer=answer,
                    latency_ms=latency_ms,
                )
            )
            await session.commit()
```

- [ ] **Крок 3: фейк у кінець `tests/fakes.py`**

Спершу додай `QueryLogRepository` до наявного імпорту з `prophet_checker.storage.interfaces`, потім:

```python
@dataclass
class LoggedQuery:
    user_id: int
    question: str
    answer: str | None
    latency_ms: int


class FakeQueryLogRepo(QueryLogRepository):
    """`fail=True` імітує лежачу БД — нею перевіряється, що запис не валить відповідь."""

    def __init__(self, fail: bool = False):
        self.entries: list[LoggedQuery] = []
        self._fail = fail

    async def save(
        self, user_id: int, question: str, answer: str | None, latency_ms: int
    ) -> None:
        if self._fail:
            raise RuntimeError("query log write failed")
        self.entries.append(LoggedQuery(user_id, question, answer, latency_ms))
```

- [ ] **Крок 4: перевірити, що нічого не зламалось**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: те саме число тестів, усе зелене (новий код ще ніхто не кличе).

- [ ] **Крок 5: коміт**

```bash
git add src/prophet_checker/storage/interfaces.py src/prophet_checker/storage/postgres.py tests/fakes.py
git commit -m "feat(storage): QueryLogRepository — Protocol, Postgres-реалізація, фейк"
```

---

## Task 3: Хендлер пише рядок (серце задачі)

**Скоуп:** тут уся поведінка й уся TDD-вага. Головна гарантія — **збій запису не заважає юзеру
отримати відповідь**; вона тестується явно.

**Files:**
- Modify: `tests/test_bot_handlers.py`
- Modify: `src/prophet_checker/bot/handlers.py`

- [ ] **Крок 1: адаптувати наявні виклики під нову сигнатуру**

`handle_question` отримує третій параметр, тож усі шість наявних викликів у
`tests/test_bot_handlers.py` перестануть відповідати сигнатурі. Додай імпорт і хелпер:

```python
from tests.fakes import FakeQueryLogRepo
```

Далі в кожному з наявних тестів заміни `await handle_question(message, orch)` на
`await handle_question(message, orch, FakeQueryLogRepo())`. Це стосується тестів:
`test_question_replies_with_answer`, `test_question_sends_typing_before_answering`,
`test_question_ignores_blank_text`, `test_question_truncates_long_answer`,
`test_question_replies_with_error_text_on_failure`.

- [ ] **Крок 2: написати нові тести, що падають**

Додай у `tests/test_bot_handlers.py` після блоку `# --- handle_question ---`:

```python
# --- query logging ---


async def test_question_logs_successful_answer():
    message = _message("що казав про Крим?")
    orch = _orchestrator("прогноз справдився")
    repo = FakeQueryLogRepo()

    await handle_question(message, orch, repo)

    assert len(repo.entries) == 1
    entry = repo.entries[0]
    assert entry.user_id == 42
    assert entry.question == "що казав про Крим?"
    assert entry.answer == "прогноз справдився"
    assert entry.latency_ms >= 0


async def test_question_logs_failure_with_null_answer():
    message = _message()
    orch = MagicMock()
    orch.answer = AsyncMock(side_effect=RuntimeError("LLM down"))
    repo = FakeQueryLogRepo()

    await handle_question(message, orch, repo)

    assert len(repo.entries) == 1
    assert repo.entries[0].answer is None
    message.answer.assert_awaited_once_with(ERROR_TEXT)


async def test_question_answers_even_if_log_write_fails():
    """Моніторинг, що кладе продукт, гірший за відсутній."""
    message = _message()
    orch = _orchestrator("прогноз справдився")

    await handle_question(message, orch, FakeQueryLogRepo(fail=True))

    message.answer.assert_awaited_once_with("прогноз справдився", parse_mode="HTML")


async def test_question_logs_raw_answer_not_truncated_message():
    long_answer = "а" * (TELEGRAM_MESSAGE_LIMIT + 500)
    message = _message()
    repo = FakeQueryLogRepo()

    await handle_question(message, _orchestrator(long_answer), repo)

    assert repo.entries[0].answer == long_answer


@pytest.mark.parametrize("text", ["   ", None])
async def test_question_does_not_log_blank_text(text):
    repo = FakeQueryLogRepo()

    await handle_question(_message(text), _orchestrator(), repo)

    assert repo.entries == []
```

- [ ] **Крок 3: запустити — переконатись, що падає**

Run: `.venv/bin/python -m pytest tests/test_bot_handlers.py -q`
Expected: FAIL — `handle_question() takes 2 positional arguments but 3 were given`.

- [ ] **Крок 4: реалізувати**

У `src/prophet_checker/bot/handlers.py` додай імпорт:

```python
from prophet_checker.storage.interfaces import QueryLogRepository
```

Додай хелпер перед `handle_question`:

```python
async def _log_query(
    repo: QueryLogRepository,
    user_id: int,
    question: str,
    answer: str | None,
    started: float,
) -> None:
    """Свій try/except: збій моніторингу не має права зламати відповідь юзеру."""
    try:
        await repo.save(
            user_id=user_id,
            question=question,
            answer=answer,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception:
        logger.exception("query log write failed (user_id=%s)", user_id)
```

Заміни `handle_question` цілком:

```python
async def handle_question(
    message: Message,
    answer_orchestrator: AnswerOrchestrator,
    query_log_repo: QueryLogRepository,
) -> None:
    if message.text is None or not message.text.strip():
        return
    user_id = message.from_user.id if message.from_user else 0
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    started = time.monotonic()
    try:
        result = await answer_orchestrator.answer(message.text)
    except Exception:
        logger.exception("bot answer failed (user_id=%s)", user_id)
        await _log_query(query_log_repo, user_id, message.text, None, started)
        await message.answer(ERROR_TEXT)
        return
    # логуємо сиру відповідь моделі, а не підрізане під ліміт Telegram повідомлення
    await _log_query(query_log_repo, user_id, message.text, result.answer, started)
    logger.info(
        "bot answer served: user_id=%s question_len=%d elapsed=%.1fs",
        user_id,
        len(message.text),
        time.monotonic() - started,
    )
    logger.debug("bot question: %s", message.text)
    await message.answer(
        compose_answer_message(result.answer, result.citations), parse_mode="HTML"
    )
```

`handle_start`, `handle_unknown_command` і `handle_non_text` не чіпаємо: вони не отримують
репозиторій у сигнатуру, тож «не логують» тут не поведінка, яку треба тестувати, а властивість
типів. Окремого тесту на них не пишемо.

- [ ] **Крок 5: запустити — має пройти**

Run: `.venv/bin/python -m pytest tests/test_bot_handlers.py -q`
Expected: PASS, усі тести файлу зелені.

- [ ] **Крок 6: перевірити гейт складності**

Run: `.venv/bin/complexipy --diff HEAD --ratchet src`
Expected: зелено. `_log_query` виділений в окремий метод навмисно — inline-`try/except` підняв би
складність `handle_question`.

- [ ] **Крок 7: коміт**

```bash
git add src/prophet_checker/bot/handlers.py tests/test_bot_handlers.py
git commit -m "feat(bot): handle_question пише слід запиту, ізольовано від відповіді"
```

---

## Task 4: Проводка

**Скоуп:** довести репозиторій від composition root до хендлера. `app.py` не чіпаємо.

**Files:**
- Modify: `tests/test_bot_runner.py`
- Modify: `src/prophet_checker/bot/runner.py`
- Modify: `src/prophet_checker/factory.py`

- [ ] **Крок 1: тест, що падає**

У `tests/test_bot_runner.py` заміни `test_build_bot_runner_wires_orchestrator_into_dispatcher`:

```python
def test_build_bot_runner_wires_dependencies_into_dispatcher():
    orch = MagicMock()
    repo = MagicMock()
    runner = build_bot_runner("123456:TEST-TOKEN", orch, repo)
    assert runner.dispatcher["answer_orchestrator"] is orch
    assert runner.dispatcher["query_log_repo"] is repo
```

- [ ] **Крок 2: запустити — переконатись, що падає**

Run: `.venv/bin/python -m pytest tests/test_bot_runner.py -q`
Expected: FAIL — `build_bot_runner() takes 2 positional arguments but 3 were given`.

- [ ] **Крок 3: розширити `build_bot_runner`**

У `src/prophet_checker/bot/runner.py` додай імпорт `QueryLogRepository` і заміни функцію:

```python
def build_bot_runner(
    token: str,
    answer_orchestrator: AnswerOrchestrator,
    query_log_repo: QueryLogRepository,
) -> BotRunner:
    bot = Bot(token=token)
    dispatcher = Dispatcher(
        answer_orchestrator=answer_orchestrator,
        query_log_repo=query_log_repo,
    )
    dispatcher.include_router(build_router())
    return BotRunner(bot, dispatcher)
```

- [ ] **Крок 4: побудувати репо у `factory.py`**

У `src/prophet_checker/factory.py` додай `PostgresQueryLogRepository` до наявного імпорту з
`prophet_checker.storage.postgres` і заміни `build_bot`:

```python
async def build_bot(
    settings: Settings, stack: AsyncExitStack, answer_orchestrator: AnswerOrchestrator
) -> BotRunner | None:
    if not settings.bot_enabled:
        return None
    if not settings.telegram_bot_token:
        raise ValueError("bot_enabled=True, але telegram_bot_token порожній")
    # engine будується після guard-ів: при вимкненому боті зайвого конекту до БД нема
    engine = make_engine(settings.database_url, settings.db_ssl_mode)
    stack.push_async_callback(engine.dispose)
    query_log_repo = PostgresQueryLogRepository(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    runner = build_bot_runner(settings.telegram_bot_token, answer_orchestrator, query_log_repo)
    stack.push_async_callback(runner.stop)
    return runner
```

- [ ] **Крок 5: запустити всю сюїту**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. Число тестів = базлайн з Task 0 + 5 (нові з Task 3; тест раннера перейменовано,
не додано).

- [ ] **Крок 6: лінт**

Run: `.venv/bin/ruff check src/prophet_checker/bot src/prophet_checker/storage src/prophet_checker/factory.py tests/test_bot_handlers.py tests/test_bot_runner.py tests/fakes.py`
Expected: зелено. (`ruff check .` на всьому дереві червоний — 68 pre-existing помилок, це відомий
борг з `progress.md`; звіряй лише свої файли.)

- [ ] **Крок 7: коміт**

```bash
git add src/prophet_checker/bot/runner.py src/prophet_checker/factory.py tests/test_bot_runner.py
git commit -m "feat(bot): проводка QueryLogRepository від composition root до хендлера"
```

---

## Task 5: Зріз `deploy/psql.sh --queries`

**Скоуп:** зробити записи видимими однією командою. Юніт-тесту нема (обґрунтування в дизайні) —
гейт це `bash -n` і `--help`.

**Files:**
- Modify: `deploy/psql.sh`

- [ ] **Крок 1: додати рядок у довідку**

Після рядка 19 (`--stats`) встав:

```bash
#   ./deploy/psql.sh --queries                          # зріз: запити до бота — хто / що / коли
```

І в рядку 13 заміни `[--stats]` на `[--stats|--queries]`.

- [ ] **Крок 2: полагодити діапазон `usage()` — легко проґавити**

`usage()` друкує коментар-блок через `sed -n '3,22p' "$0"`. Крок 1 додав рядок, тож блок тепер
закінчується на 23. Знайди в `usage()` і заміни:

```bash
usage() { sed -n '3,23p' "$0" | sed 's/^# \{0,1\}//'; }
```

Без цієї правки `--help` мовчки з'їсть останній рядок конфігу.

- [ ] **Крок 3: додати `QUERIES_SQL`**

`STATS_SQL` починається на рядку 51 і закінчується самотньою лапкою на рядку 105 (номери — станом
на `main` до цієї роботи; звір очима, а не наосліп). Одразу після неї, перед першою функцією, встав:

```bash
QUERIES_SQL="
-- Обсяг і аудиторія за два вікна, по рядку на вікно — щоб порівнювати очима.
-- failed = збій до відповіді (answer is null); частку відмов бота цей зріз не знає
-- за побудовою (див. дизайн, секція «Що навмисно НЕ входить»).
select '24h' as window,
       count(*)                               as queries,
       count(distinct user_id)                as users,
       count(*) filter (where answer is null) as failed,
       percentile_disc(0.5)  within group (order by latency_ms) as p50_ms,
       percentile_disc(0.95) within group (order by latency_ms) as p95_ms
from query_logs where created_at > now() - interval '24 hours'
union all
select '7d',
       count(*),
       count(distinct user_id),
       count(*) filter (where answer is null),
       percentile_disc(0.5)  within group (order by latency_ms),
       percentile_disc(0.95) within group (order by latency_ms)
from query_logs where created_at > now() - interval '7 days';

-- Найактивніші за тиждень: чи це органіка, чи один ентузіаст робить весь трафік.
select user_id,
       count(*)       as queries,
       max(created_at) as last_seen
from query_logs
where created_at > now() - interval '7 days'
group by user_id
order by queries desc
limit 10;

-- Що саме питають. Обрізаємо до 80 символів, інакше таблиця нечитабельна в терміналі.
select date_trunc('second', created_at)       as at,
       user_id,
       left(question, 80)                     as question,
       coalesce(left(answer, 80), '(збій)')   as answer,
       latency_ms
from query_logs
order by created_at desc
limit 20;
"
```

- [ ] **Крок 4: замінити диспетч у `main()`**

Наявний `main()` тримає прапорець `stats=0` і розгалужується в кінці. Дві опції в `if`/`elif`
читались би гірше, тож тримаємо одну змінну з текстом SQL. Заміни початок `main()`:

```bash
main() {
  local sql=""
  case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --stats)   sql="$STATS_SQL";   shift ;;
    --queries) sql="$QUERIES_SQL"; shift ;;
  esac
```

І блок у кінці `main()`:

```bash
  # SQL іде stdin-ом (-f -), а не -c: так psql друкує результат КОЖНОГО запиту,
  # а не лише останнього.
  if [ -n "$sql" ]; then
    psql -v ON_ERROR_STOP=1 "$@" -f - <<<"$sql"
  else
    psql "$@"
  fi
}
```

- [ ] **Крок 5: перевірити**

```bash
bash -n deploy/psql.sh
./deploy/psql.sh --help
```

Expected: `bash -n` мовчить. `--help` друкує довідку, в ній видно і `--queries`, і останній рядок
про `SECRETS_STACK, SECRETS_BUCKET, ENV_KEY, LOCAL_PORT` (доказ, що діапазон із кроку 2 правильний).

- [ ] **Крок 6: коміт**

```bash
git add deploy/psql.sh
git commit -m "feat(deploy): psql.sh --queries — зріз запитів до бота"
```

---

## Task 6: Смоук на живій БД, документація, зведення

**Скоуп:** довести, що ланцюг працює наскрізь (не лише на фейках), і лишити слід у документації.

**Files:**
- Modify: `runbook/bot.md`, `progress.md`, `docs/README.md`

- [ ] **Крок 1: локальний end-to-end смоук**

Це єдиний крок, що перевіряє `PostgresQueryLogRepository` — фейки його не покривають.

```bash
docker compose up -d
.venv/bin/alembic upgrade head
```

У `.env` виставь `BOT_ENABLED=true` і справжній `TELEGRAM_BOT_TOKEN`, тоді:

```bash
.venv/bin/python -m prophet_checker
```

Напиши боту одне питання в Telegram, дочекайся відповіді, тоді:

```bash
docker compose exec -T postgres psql -U prophet -d prophet \
  -c 'select user_id, left(question,40), left(answer,40), latency_ms, created_at from query_logs;'
```

Expected: рівно один рядок, з твоїм `user_id`, твоїм питанням, непорожньою відповіддю і
`latency_ms` у тисячах (RAG-запит іде секунди). Після цього поверни `BOT_ENABLED` як було.

- [ ] **Крок 2: перевірити, що бот виживає без таблиці**

Головна гарантія дизайну, і фейк її доводить лише на рівні юніту. Перевіримо на живому:

```bash
docker compose exec -T postgres psql -U prophet -d prophet -c 'alter table query_logs rename to query_logs_tmp;'
```

Перезапусти застосунок, напиши боту ще одне питання.

Expected: **юзер отримує нормальну відповідь**, у лозі з'являється `query log write failed`
з трейсбеком. Поверни назад:

```bash
docker compose exec -T postgres psql -U prophet -d prophet -c 'alter table query_logs_tmp rename to query_logs;'
```

- [ ] **Крок 3: дописати runbook**

У `runbook/bot.md` додай секцію в кінець:

```markdown
## Моніторинг запитів

Кожне змістовне питання до бота лишає рядок у `query_logs` (прод-RDS). Дивитись:

    ./deploy/psql.sh --queries

Друкує за вікна 24г і 7д: кількість запитів, унікальних користувачів, збої (`answer is null`),
p50/p95 латентності; далі топ-10 активних користувачів за тиждень і останні 20 запитів текстом.

`/start` і не-текстові повідомлення не логуються. Частка **відмов** бота (коли він не знайшов
даних) не рахується — свідоме рішення, див. `docs/observability/2026-07-20-query-logging-design.md`.

Збій запису не впливає на відповідь юзеру — шукай `query log write failed` у `./deploy/logs.sh`.
```

- [ ] **Крок 4: дописати `progress.md`**

У секцію `## Notes` додай запис (дзеркалить стиль наявних):

```markdown
- **Query logging — слід публічних запитів бота (2026-07-20).** Перед відкриттям бота на загал
  не було відповіді на «що люди питають»: текст запиту писався на `DEBUG` при `log_level=INFO`,
  а решта жила в `docker compose logs` до наступного `deploy.sh`. Закрито таблицею `query_logs`
  (`user_id` **BigInteger** — Telegram id не влазить в int32; `question`, `answer` nullable,
  `latency_ms`, `created_at` + індекс) і зрізом `deploy/psql.sh --queries`. Запис робить
  `handle_question` в обох гілках; виклик у `try/except` на місці виклику — **збій моніторингу
  не валить відповідь**, і це окремий тест. Доменної моделі навмисно нема: запис їде одним хопом
  і читається лише через psql, тож міст domain↔db був би церемонією. **Свідомо не входить:**
  класифікація вердикту (⇒ частку відмов заднім числом не порахувати) і `source_ids`
  (⇒ крива відповідь видима, але не відтворювана) — обидва відхилив користувач, наслідки
  зафіксовані в дизайні. Design+plan: [`docs/observability/`](docs/observability/).
```

- [ ] **Крок 5: додати план в індекс `docs/README.md`**

У таблицю секції `## 📊 observability/` додай рядок:

```markdown
| [`2026-07-20-query-logging-plan.md`](observability/2026-07-20-query-logging-plan.md) | Implementation plan — 6 тасків TDD |
```

- [ ] **Крок 6: фінальна верифікація**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/complexipy --diff HEAD --ratchet src
bash -n deploy/psql.sh
```

Expected: сюїта зелена (базлайн + 5), ratchet зелений, `bash -n` мовчить.

- [ ] **Крок 7: коміт і зведення**

```bash
git add runbook/bot.md progress.md docs/README.md
git commit -m "docs(observability): runbook + progress по query-logging"
git checkout main && git merge --ff-only feat/query-logging
```

- [ ] **Крок 8: деплой (за рішенням користувача)**

Міграція застосується на проді при наступному деплої. Порядок і застереження — у `runbook/deploy.md`.

⚠️ Нагадування з `progress.md`: перед будь-яким `update-stack` на `prophet-compute` запінити AMI —
`LatestAmiId` резолвиться на найновіший AL2023, а живий бокс на старішому, тож апдейт пересоздасть
інстанс і вб'є бокс. Звичайний `deploy.sh` стека не чіпає, але якщо йтимеш через CloudFormation —
пам'ятай.
