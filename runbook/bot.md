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
- **Дані в БД.** Порожній корпус → бот на кожне питання відповідає відмовою. Це
  коректно, але не те, що хочеш побачити на смоуку. Засівання — `runbook/first-ingest.md`.
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
| Бот на все відповідає відмовою | У БД нема прогнозів (засій — `first-ingest.md`) або relevance-поріг усе відсіює. |
| Кожне питання → `⚠️ Щось пішло не так...` | `answer()` кинув (LLM / БД / ключі). Дивись `logger.exception` у логах застосунку. |
| Застосунок висить або падає на старті (до `Start polling`) | Колектор: нема `tg_session.session` чи `TELEGRAM_API_ID/HASH`, сесія відкликана, або auth-key конфлікт із живим боксом. |

## Зупинка

`Ctrl+C`. uvicorn закриває lifespan → `BotRunner.stop()` глушить polling і закриває сесію
бота (graceful, без сирих трейсбеків).

## Прод (EC2-бокс)

1. Поклади `TELEGRAM_BOT_TOKEN=<токен>` і `BOT_ENABLED=true` в env-файл секретів у
   приватному S3 (див. `docs/aws-deploy/`).
2. Перезапусти compose на боксі — потягне свіжі секрети.
3. Смоук з телефона (розділ «Перевірка», п. 3).

Бот живе, поки живий бокс. Для білінгового бокса, який гасять, це очікувано.

## Додаток: смоук лише бота, без колектора

Коли бокс живий (і чіпати колектор-сесію не можна) або треба швидкий цикл — підійми
компоненти напряму, оминаючи `build_orchestrator`. Колектор не конектиться, ризику
auth-key нема:

```python
import asyncio
from contextlib import AsyncExitStack
from prophet_checker.config import Settings
from prophet_checker.factory import build_answer_orchestrator, build_bot_runner

async def main():
    settings = Settings()
    async with AsyncExitStack() as stack:
        ao = await build_answer_orchestrator(settings, stack)
        runner = build_bot_runner(settings.telegram_bot_token, ao)
        await runner.start()          # реальний токен -> бот слухає; пиши йому з телефона
        await asyncio.Event().wait()  # Ctrl+C щоб зупинити

asyncio.run(main())
```

Потрібні лише `OPENAI_API_KEY`, `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN` і дані в БД —
`TELEGRAM_API_ID/HASH` і `tg_session.session` не потрібні.

---

_Перевірено 2026-07-12 (гілка `feat/telegram-bot`):_

- _20/20 юніт-тестів бота; гарди `build_bot` (вимкнено → `None`, увімкнено без токена → `ValueError`) і `build_bot_runner`._
- _Мозок `answer()` — live на 173 прогнозах: 10 джерел, реальна відповідь ~585 символів._
- _Wiring polling — live до `getMe`: `Start polling` → з невірним токеном `CRITICAL ... Unauthorized`. Валідний шлях відрізняється лише успішним `getMe` → `Run polling for bot @...`._
- _Повний `python -m prophet_checker` з конектом колектора навмисно не ганявся (захист user-сесії від конфлікту з боксом); смоук з телефона — ручний крок._
