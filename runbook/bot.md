# Runbook — Telegram-бот (локальний запуск і перевірка)

Бот — тонкий aiogram-фронтенд над готовим `AnswerOrchestrator`. Він **не окремий
процес**: long-polling крутиться asyncio-таскою всередині FastAPI-застосунку.
«Запустити бота» = запустити застосунок з `BOT_ENABLED=true` і `TELEGRAM_BOT_TOKEN`.
Код бота — на гілці `feat/telegram-bot`.

Кожне текстове повідомлення бот віддає в `AnswerOrchestrator.answer()` — **той самий
мозок, що обслуговує `POST /answer`**. Тому відповіді бота можна перевірити через
`curl`, без телефона.

## Разово: створити бота

1. У Telegram → `@BotFather` → `/newbot` → задай ім'я і username.
2. Збережи токен виду `123456789:AA...`. Це секрет — тільки в `.env` (локально) або
   в S3-секретах (прод).

## Передумови локального запуску

Повний старт застосунку піднімає весь пайплайн, не лише бота. Тому потрібно:

- **`.venv` (uv) і Postgres.** `docker compose up -d` (контейнер `prophet_postgres`),
  далі `.venv/bin/alembic upgrade head`.
- **Дані в БД.** Порожній корпус → бот на кожне питання відповідає відмовою. Наповни
  локальну БД перед смоуком — див. [`ingest.md`](ingest.md).
- **Ключі в `.env`:**
  - `OPENAI_API_KEY`, `GEMINI_API_KEY` — мозок відповідей (embeddings + генерація);
  - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` + файл `tg_session.session` — потрібні **не
    боту, а колектору**: старт застосунку робить `tg_client.start()` ще до збірки бота
    (див. caveat нижче);
  - `TELEGRAM_BOT_TOKEN` — токен з BotFather;
  - `BOT_ENABLED=true` — вмикає бота (дефолт `false`, щоб дев і тести його не піднімали).

> **⚠️ Колектор-сесія.** Повний старт `python -m prophet_checker` конектить Telethon
> user-сесію колектора. Цей самий `tg_session` лежить і в S3 / на EC2-боксі. Один
> auth-key, активний з двох IP одночасно, Telegram може розцінити як компрометацію і
> **розлогінити акаунт** (відновлення — повторна авторизація з телефона). Тому підіймай
> локально лише коли **бокс зупинено**. Хочеш смоукнути **лише бота** без колектора —
> див. додаток у кінці.

## Запуск

1. У `.env`: `BOT_ENABLED=true` і `TELEGRAM_BOT_TOKEN=<токен>`.
2. `.venv/bin/python -m prophet_checker` (uvicorn на `127.0.0.1:8000`).

Успішний старт у логах:

```
INFO  ... Application startup complete.
INFO  aiogram.dispatcher: Start polling
INFO  aiogram.dispatcher: Run polling for bot @<твій_бот> id=<...> - '<ім'я>'
```

Рядок `Run polling for bot @<username>` = бот на зв'язку і слухає апдейти.

## Перевірка

Три шари — від найдешевшого до повного.

**1. Процес живий:**

```
curl -s localhost:8000/health          # -> {"status":"ok"}
```

**2. Мозок відповідей — без телефона** (та сама гілка, що й у бота):

```
curl -s -X POST localhost:8000/answer \
  -H 'Content-Type: application/json' \
  -d '{"question":"Що Арестович прогнозував про завершення війни?"}'
```

Очікуєш JSON з `answer` (кілька речень + дисклеймер про автоматичний аналіз) і непорожнім
`sources`. У логах — `answer_from_sources: generated from N sources`.

**3. Смоук з телефона** (потрібен валідний токен):

| Надсилаєш | Очікуєш |
|-----------|---------|
| `/start` або `/help` | привітання зі списком прикладів питань |
| питання текстом | індикатор «печатає…», за кілька секунд відповідь |
| невідома команда (`/foo`) | «Не знаю такої команди. Просто напиши питання текстом.» |
| стікер / фото / голосове | «Я розумію лише текстові питання…» |
| порожній / пробіли | нічого (бот ігнорує, LLM не викликається) |

## Діагностика збоїв

| Симптом | Причина і дія |
|---------|---------------|
| `CRITICAL ... bot polling task died: ... Unauthorized` | Невірний `TELEGRAM_BOT_TOKEN`. HTTP-API живе далі — мертвий тільки бот. Онови токен, перезапусти. |
| `ValueError: bot_enabled=True, але telegram_bot_token порожній` (падає на старті) | `BOT_ENABLED=true`, але токен не заданий. Додай `TELEGRAM_BOT_TOKEN`. |
| Бот на все відповідає відмовою | У БД нема прогнозів (наповни — [`ingest.md`](ingest.md)) або relevance-поріг усе відсіює. |
| Кожне питання → `⚠️ Щось пішло не так...` | `answer()` кинув (LLM / БД / ключі). Дивись `logger.exception` у логах застосунку. |
| Застосунок висить або падає на старті (до `Start polling`) | Колектор: нема `tg_session.session` чи `TELEGRAM_API_ID/HASH`, сесія відкликана, або auth-key конфлікт із живим боксом. |

## Зупинка

`Ctrl+C`. uvicorn закриває lifespan → `BotRunner.stop()` глушить polling і закриває сесію
бота (graceful, без сирих трейсбеків).

## Прод (EC2-бокс)

1. Поклади токен і прапорець у S3-секрети (точкова правка, решта ключів ціла):
   ```bash
   ./deploy/secrets.sh set TELEGRAM_BOT_TOKEN <токен>
   ./deploy/secrets.sh set BOT_ENABLED true
   ```
2. Підтягни свіжі секрети на бокс і перезапусти застосунок:
   ```bash
   ./deploy/refresh.sh
   ```
   Бокс копіює `.env` з S3 **лише на bootstrap** — простий рестарт compose свіжі секрети
   НЕ підтягне. Деплой коду `./deploy/deploy.sh` робить цей рефреш заразом.
3. Перевір, що бот піднявся, і смоукни з телефона:
   ```bash
   ./deploy/logs.sh | grep -i polling      # -> Run polling for bot @<username>
   ```
   Далі — розділ «Перевірка», п. 3.

Бот живе, поки живий бокс. Для білінгового бокса, який гасять, це очікувано.

## Додаток: смоук лише бота, без колектора

Коли бокс живий (і чіпати колектор-сесію не можна) або треба швидкий цикл — підійми
компоненти напряму, оминаючи `build_orchestrator`. Колектор не конектиться, ризику
auth-key нема:

```python
import asyncio
from contextlib import AsyncExitStack
from sqlalchemy.ext.asyncio import async_sessionmaker
from prophet_checker.config import Settings
from prophet_checker.factory import build_answer_orchestrator, build_bot_runner
from prophet_checker.storage.engine import make_engine
from prophet_checker.storage.postgres import PostgresQueryLogRepository

async def main():
    settings = Settings()
    async with AsyncExitStack() as stack:
        ao = await build_answer_orchestrator(settings, stack)
        engine = make_engine(settings.database_url, settings.db_ssl_mode)
        stack.push_async_callback(engine.dispose)
        query_log_repo = PostgresQueryLogRepository(async_sessionmaker(engine, expire_on_commit=False))
        runner = build_bot_runner(settings.telegram_bot_token, ao, query_log_repo)
        await runner.start()          # реальний токен -> бот слухає; пиши йому з телефона
        await asyncio.Event().wait()  # Ctrl+C щоб зупинити

asyncio.run(main())
```

Потрібні лише `OPENAI_API_KEY`, `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN` і дані в БД —
`TELEGRAM_API_ID/HASH` і `tg_session.session` не потрібні.

## Моніторинг запитів

Кожне змістовне питання до бота лишає рядок у таблиці `query_logs` (`user_id`,
`question`, `answer`, `latency_ms`, `created_at`). Дивитись — однією командою:

```bash
./deploy/psql.sh --queries
```

Друкує три блоки: за вікна 24г і 7д — кількість запитів, унікальних користувачів,
збоїв (`answer is null`) і p50/p95 латентності; далі топ-10 активних користувачів за
тиждень; далі останні 20 запитів текстом.

Що цей зріз **не** знає: частку **відмов** — коли бот відпрацював, але даних не знайшов.
Свідоме рішення, наслідки записані в [`docs/observability/2026-07-20-query-logging-design.md`](../docs/observability/2026-07-20-query-logging-design.md).
`failed` у звіті — це збій до відповіді, не відмова.

`/start`, невідомі команди й не-текстові повідомлення не логуються: вони не кажуть,
що людей цікавить, і лише засмічують вибірку.

**Збій запису не впливає на відповідь юзеру** — це гарантія дизайну, покрита тестом.
Якщо БД лягла, бот відповідає далі, а в лозі з'являється `query log write failed`:

```bash
./deploy/logs.sh | grep 'query log write failed'
```

---

_Перевірено 2026-07-12 (гілка `feat/telegram-bot`):_

- _20/20 юніт-тестів бота; гарди `build_bot` (вимкнено → `None`, увімкнено без токена → `ValueError`) і `build_bot_runner`._
- _Мозок `answer()` — live на 173 прогнозах: 10 джерел, реальна відповідь ~585 символів._
- _Wiring polling — live до `getMe`: `Start polling` → з невірним токеном `CRITICAL ... Unauthorized`. Валідний шлях відрізняється лише успішним `getMe` → `Run polling for bot @...`._
- _Повний `python -m prophet_checker` з конектом колектора навмисно не ганявся (захист user-сесії від конфлікту з боксом); смоук з телефона — ручний крок._

_Оновлено 2026-07-13: прод-кроки переписані під `deploy/refresh.sh` — рестарт compose сам свіжі секрети з S3 не тягне (бокс копіює `.env` лише на bootstrap)._

_Оновлено 2026-07-20: додано секцію «Моніторинг запитів» (`query_logs` + `psql.sh --queries`). Сніпет у додатку оновлено — `build_bot_runner` тепер приймає третім аргументом `QueryLogRepository`, зі старим викликом на два аргументи він падав би `TypeError`._
