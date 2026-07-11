# Telegram-бот v1 — Design

**Дата:** 2026-07-11
**Статус:** затверджено (brainstorm-сесія 2026-07-11)
**Трек:** bot (новий; остання миля над `query-serving` + `generation`)
**Основа:** [`../architecture/2026-04-26-flow-production-rag.md`](../architecture/2026-04-26-flow-production-rag.md) (ранній флоу-док), answer-contract ([`../generation/2026-06-29-rag-answer-contract-design.md`](../generation/2026-06-29-rag-answer-contract-design.md))

---

## 1. Проблема

Увесь продуктовий пайплайн уже працює: collect → extract → verify → `POST /answer`
(RAG-відповідь за answer-contract). Бракує останньої милі — інтерфейсу,
яким продукт може користуватися людина, а не curl. Telegram-бот —
запланований фронтенд із Phase 7 (`progress.md`) і головне демо проєкту
для портфоліо.

## 2. Рішення (огляд)

Тонкий Telegram-фронтенд над готовим `AnswerOrchestrator`:

- **aiogram 3.x**, **long-polling** — вихідний конект до api.telegram.org.
- Бот живе **в наявному FastAPI-процесі**: polling — asyncio-таска в lifespan.
- Хендлер вільного тексту викликає `AnswerOrchestrator.answer()` **напряму**
  (без HTTP-хопа) — оркестратор уже збирається в composition root.
- Кожне повідомлення — незалежне питання (**stateless**, без історії розмови).

## 3. Скоуп

**У v1:**

- Пакет `src/prophet_checker/bot/`: texts, handlers, runner.
- Команди `/start`, `/help`; вільний текст → RAG-відповідь; ввічливі
  фолбеки на не-текст і невідомі команди.
- Конфіг `bot_token` + `bot_enabled`; wiring у `factory.py` / `app.py`.
- Юніт-тести хендлерів на фейках; ручний смоук на живому боті.
- Рансетап: `BOT_TOKEN` у секрети, `runbook/bot.md`.

**Свідомо поза скоупом (park):**

- Rate-limit / захист від витрат — публічний бот без лімітів; повернутися,
  якщо з'явиться реальний трафік.
- Multi-turn, кешування відповідей, уточнення vague-запитів (перекочували
  з флоу-доку 2026-04-26 — лишаються відкладеними).
- Браузинг-команди (`/people`, `/recent`, `/stats`) — наступна ітерація.
- Ім'я автора + прямий лінк на канал у відповіді (Person-join; чип
  `task_ea2d0fea`) — відповіді лишаються author-agnostic за контрактом.
- Webhook-режим — потребує публічного HTTPS, якого на SSH-only боксі немає.

## 4. Ключові рішення й чому

| Рішення | Чому |
|---------|------|
| Long-polling, не webhook | Бокс SSH-only, без публічного HTTP-інгресу. Webhook = публічний HTTPS + сертифікат + домен; polling = нуль змін в інфрі |
| У FastAPI-процесі, не окремий сервіс | Тонкому фронтенду ізоляція нічого не дає, а другий контейнер на білінговому t3.small, другий composition root і зміни в compose — реальна ціна |
| aiogram 3.x, не Telethon bot-mode | Telethon уже є, але це MTProto user-client: бот-режим неідіоматичний, без роутерів/команд/DI; плюс ризик сплутати bot-identity з колекторською user-сесією |
| Публічний доступ без лімітів | Портфоліо-демо: лінк піде в LinkedIn-пости, тертя на вході неприйнятне. Flash Lite дешевий; ліміти — окрема ітерація за потреби |
| Author-agnostic відповіді | Точно за чинним answer-contract. Корпус зараз — один автор, втрата мінімальна |
| Незалежність від hybrid-retrieval Частини B | Бот викликає лише `answer(query)`; QueryPlanner міняє нутрощі retrieval — бот отримає покращення прозоро. Порядок мерджів треків байдужий |

## 5. Компоненти й інтерфейси

```
src/prophet_checker/bot/
  __init__.py
  texts.py       — START_TEXT, ERROR_TEXT, NOT_TEXT_TEXT, UNKNOWN_COMMAND_TEXT
  handlers.py    — хендлери повідомлень (чисті async-функції)
  runner.py      — збірка Bot + Dispatcher, старт/стоп polling
```

Сигнатури (контракти; тіла — у плані):

```python
# runner.py
def build_bot_runner(token: str, answer_orchestrator: AnswerOrchestrator) -> BotRunner: ...

class BotRunner:
    async def start(self) -> None: ...   # створює polling-таску (handle_signals=False)
    async def stop(self) -> None: ...    # зупиняє polling, закриває сесію бота

# handlers.py
async def handle_start(message: Message) -> None: ...        # /start і /help → START_TEXT
async def handle_unknown_command(message: Message) -> None: ...
async def handle_question(message: Message, answer_orchestrator: AnswerOrchestrator) -> None: ...
async def handle_non_text(message: Message) -> None: ...
```

- **DI:** `Dispatcher(answer_orchestrator=...)` — aiogram інджектить залежність
  у хендлер за ім'ям типізованого параметра. Без глобалів.
- **Порядок роутингу:** команди → невідома команда (текст, що починається
  з `/`) → вільний текст → усе інше (не-текст).
- **Конфіг (`config.py`):** `bot_token: str | None = None`,
  `bot_enabled: bool = False`. Дефолт вимкнено — локальний дев і тести
  не зачеплені.
- **Wiring:** `factory.py` (composition root) при `bot_enabled=True` збирає
  `BotRunner`, реєструє `stop()` в `AsyncExitStack` і повертає runner поруч
  з оркестраторами (`None`, якщо вимкнено); `app.py` lifespan викликає
  `start()` поруч зі стартом HTTP. `handle_signals=False` — сигналами
  володіє uvicorn.
- **`START_TEXT` мусить містити:** що це за бот, чиї прогнози в корпусі,
  2–3 приклади питань, дисклеймер про автоматичний аналіз.

## 6. Потік даних (вільний текст)

1. Юзер надсилає текст.
2. Хендлер шле `chat_action="typing"` (RAG триває секунди).
3. `answer_orchestrator.answer(text)` → `AnswerResult`.
4. Відповідь = `result.answer` **plain text, без parse_mode** — текст уже
   user-ready за answer-contract; вимкнений markdown-парсинг прибирає клас
   помилок Telegram на випадкових символах.
5. Понад 4096 символів (ліміт Telegram) — обрізати з «…» так, щоб разом
   з ним вкластися в 4096. За контрактом відповіді короткі; різати на
   серію повідомлень — YAGNI.

Refusal-кейс окремої гілки не потребує: `AnswerOrchestrator` уже
short-circuit'ить порожні джерела текстом відмови — бот його ретранслює.

## 7. Поведінка на вході (таблиця)

| Вхід | Реакція |
|------|---------|
| `/start`, `/help` | `START_TEXT` |
| Невідома команда (`/foo`) | `UNKNOWN_COMMAND_TEXT`: «просто напиши питання текстом» |
| Вільний текст | typing → RAG-відповідь |
| Порожній / пробільний текст | ігнорувати (без LLM-виклику) |
| Не-текст (стікер, фото, войс) | `NOT_TEXT_TEXT`: «я розумію лише текстові питання» |

Конкурентність: aiogram обробляє кожен update окремою таскою; `answer()` —
той самий stateless-шлях, що вже обслуговує конкурентні HTTP-запити
`/answer`. Нових вимог бот не додає.

## 8. Помилки

| Збій | Поведінка |
|------|-----------|
| `answer()` кидає (LLM/БД недоступні) | Хендлер — boundary: broad `except` → `logger.exception` + відповідь `ERROR_TEXT`. Нутрощі юзеру не течуть, бот живе |
| Мережеві збої polling | aiogram реконектиться сам |
| Polling-таска померла | `CRITICAL` у лог; API живе далі; бот оживе з деплоєм. Супервізор-самовідновлювач — YAGNI |
| `bot_enabled=True`, токена немає | Fail fast у composition root на старті (патерн Р4 hybrid-retrieval) |

## 9. Логування

- `INFO`: user_id, довжина питання, латентність відповіді.
  Текст питання — лише `DEBUG` (user content, за правилом «без raw payloads»).
- Логери — стандартно per-module; конфігурація логування не змінюється.

## 10. Тестування

Юніт-тести (фейки, без мережі й Docker; aiogram `Message` — pydantic-модель,
конструюється в тесті; `reply`/`answer` — стаби із захопленням викликів):

1. `/start` → `START_TEXT`.
2. `/help` → те саме.
3. Вільний текст → реплай = `AnswerResult.answer`.
4. Відповідь >4096 → обрізана з «…».
5. Не-текст → `NOT_TEXT_TEXT`.
6. Порожній текст → без реплая і без виклику `answer()`.
7. Оркестратор кидає → `ERROR_TEXT`, хендлер не падає.
8. `bot_enabled=True` без токена → fail fast.
9. Typing-екшн надіслано перед `answer()`.

Polling-луп юніт-тестами не ганяємо. Ручний смоук: підняти локально
з тестовим токеном → написати боту → відповідь за контрактом.

## 11. Рансетап і деплой

1. Створити бота через BotFather (разово, руками) → токен.
2. `BOT_TOKEN` → `.env.example` (порожній) + S3-секрети бокса.
3. На боксі `bot_enabled=true`; локально вмикається лише для смоуку.
4. Короткий `runbook/bot.md` із цими кроками.

Compose і CloudFormation — **без змін**. Бот живе стільки, скільки бокс:
для білінгового бокса, який гасять, це очікувана поведінка.

## 12. Відкриті питання (на майбутнє, не блокери v1)

- Rate-limit і антиспам, коли з'явиться трафік.
- Браузинг-команди й discoverability корпусу.
- Автор + лінк на канал (Person-join, чип `task_ea2d0fea`).
- Multi-turn і кешування популярних питань.
