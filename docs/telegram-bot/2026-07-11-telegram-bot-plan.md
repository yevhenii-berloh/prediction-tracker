# Telegram-бот v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** у Telegram працює публічний бот: `/start` пояснює, що питати; вільне текстове питання повертає RAG-відповідь за answer-contract; не-текст і невідомі команди отримують ввічливі підказки.

**Architecture:** за дизайном [`2026-07-11-telegram-bot-design.md`](2026-07-11-telegram-bot-design.md) (рішення й таблиці там, план їх не пере-аргументує). Пакет `bot/` (texts → handlers → runner), aiogram 3 long-polling asyncio-таскою в lifespan FastAPI, хендлер питання напряму викликає `AnswerOrchestrator.answer()`. Залежності не змінюються: aiogram і `telegram_bot_token` уже в проєкті.

**Tech Stack:** Python 3.14, aiogram ≥3.13 (уже в `pyproject.toml`), pytest (`asyncio_mode=auto`, MagicMock/AsyncMock, без мережі й Docker).

**Гілка:** `feat/telegram-bot`. Усі команди — з кореня worktree
`/Users/evgenijberlog/Brain/prediction-tracker/.claude/worktrees/feat+telegram-bot`.

**Гейти на кожен коміт:** pre-commit хук жене complexipy ratchet автоматично; перед комітом руками — `.venv/bin/ruff check src tests` (зелений на змінених файлах).

**Квіз у кожному таску:** наприкінці кожного таска — блок самоперевірки для рев'юера
плану. Мета: пройти квіз ДО погодження виконання і впевнитись, що суть зміни зрозуміла.
Відповіді сховані в розгортці під питаннями. Виконавця плану квізи не стосуються.

---

### Task 1: `bot/texts.py` — статичні тексти і truncate

**Скоуп:** новий пакет `bot/` з константами текстів і хелпером обрізання під ліміт Telegram (design §5, §6 крок 5). Guard-тест фіксує обов'язкові елементи `START_TEXT` (design §5, останній булет) — за патерном guard-тесту answer-contract.

**Files:**
- Create: `src/prophet_checker/bot/__init__.py` (порожній)
- Create: `src/prophet_checker/bot/texts.py`
- Test: `tests/test_bot_texts.py` (новий)

- [ ] **Step 1: Написати failing-тести**

Створити `tests/test_bot_texts.py`:

```python
from prophet_checker.bot.texts import (
    START_TEXT,
    TELEGRAM_MESSAGE_LIMIT,
    truncate_for_telegram,
)

# --- truncate_for_telegram ---


def test_truncate_returns_short_text_unchanged():
    assert truncate_for_telegram("коротка відповідь") == "коротка відповідь"


def test_truncate_keeps_text_at_exact_limit():
    text = "а" * TELEGRAM_MESSAGE_LIMIT
    assert truncate_for_telegram(text) == text


def test_truncate_cuts_overflow_to_limit_with_ellipsis():
    result = truncate_for_telegram("а" * (TELEGRAM_MESSAGE_LIMIT + 1))
    assert len(result) == TELEGRAM_MESSAGE_LIMIT
    assert result.endswith("…")


# --- START_TEXT: guard на обов'язкові елементи (design §5) ---


def test_start_text_has_required_elements():
    assert "Арестович" in START_TEXT  # чий корпус
    assert "автоматизований" in START_TEXT  # дисклеймер
    assert START_TEXT.count("?") >= 2  # приклади питань
```

- [ ] **Step 2: Переконатися, що тести падають**

Run: `.venv/bin/python -m pytest tests/test_bot_texts.py -q`
Expected: 4 errors, `ModuleNotFoundError: No module named 'prophet_checker.bot'`

- [ ] **Step 3: Імплементація**

Створити порожній `src/prophet_checker/bot/__init__.py`.

Створити `src/prophet_checker/bot/texts.py`:

```python
"""Статичні тексти бота і хелпер під ліміт повідомлення Telegram."""

TELEGRAM_MESSAGE_LIMIT = 4096

START_TEXT = (
    "Привіт! Я бот проєкту prediction-tracker.\n"
    "\n"
    "Я відповідаю на питання про прогнози українських публічних осіб "
    "і кажу, чи вони справдилися. Зараз у базі — прогнози Олексія "
    "Арестовича з його Telegram-каналу.\n"
    "\n"
    "Спробуй спитати:\n"
    "• Що Арестович прогнозував про завершення війни?\n"
    "• Які прогнози про Крим справдилися?\n"
    "• Що він казав про F-16?\n"
    "\n"
    "Аналіз автоматизований і може містити неточності."
)

ERROR_TEXT = "⚠️ Щось пішло не так. Спробуй ще раз трохи пізніше."

NOT_TEXT_TEXT = "Я розумію лише текстові питання — напиши, будь ласка, словами."

UNKNOWN_COMMAND_TEXT = "Не знаю такої команди. Просто напиши питання текстом."


def truncate_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
```

- [ ] **Step 4: Тести зелені**

Run: `.venv/bin/python -m pytest tests/test_bot_texts.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check src/prophet_checker/bot tests/test_bot_texts.py
git add src/prophet_checker/bot tests/test_bot_texts.py
git commit -m "feat(bot): тексти і truncate під ліміт Telegram"
```

**Квіз (самоперевірка перед виконанням)**

1. Чому обрізаємо до `limit - 1` символів + «…», а не до `limit`?
   - А) Щоб лишити місце під markdown-розмітку
   - +Б) «…» — теж символ: разом із ним відповідь мусить вкластися в 4096 (design §6 крок 5)
   - В) Ліміт Telegram насправді 4095
2. Чому guard-тест перевіряє лише `START_TEXT`, а не всі чотири константи?
   - А) Забули — треба додати на всі
   - +Б) Лише для `START_TEXT` дизайн фіксує обов'язкові елементи; решта — довільні фрази, їхнє використання перевіряють хендлер-тести
   - В) Інші константи приватні

<details><summary>Відповіді</summary>

1. **Б** — «обрізати з «…» так, щоб разом з ним вкластися в 4096».
2. **Б** — тест на «константа непорожня» нічого не ловить; контракт є тільки у `START_TEXT`.

</details>

---

### Task 2: прості хендлери — `/start`, невідома команда, не-текст

**Скоуп:** три тривіальні хендлери-відповідачі (рядки 1, 2, 5 UX-таблиці design §7). Хендлери — чисті async-функції: в тестах aiogram-машинерія не потрібна, повідомлення — MagicMock.

**Files:**
- Create: `src/prophet_checker/bot/handlers.py`
- Test: `tests/test_bot_handlers.py` (новий)

- [ ] **Step 1: Написати failing-тести**

Створити `tests/test_bot_handlers.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from prophet_checker.bot.handlers import (
    handle_non_text,
    handle_start,
    handle_unknown_command,
)
from prophet_checker.bot.texts import NOT_TEXT_TEXT, START_TEXT, UNKNOWN_COMMAND_TEXT


def _message(text="питання"):
    message = MagicMock()
    message.text = text
    message.answer = AsyncMock()
    message.bot.send_chat_action = AsyncMock()
    message.chat.id = 1
    message.from_user.id = 42
    return message


# --- прості хендлери ---


async def test_handle_start_replies_with_start_text():
    message = _message("/start")
    await handle_start(message)
    message.answer.assert_awaited_once_with(START_TEXT)


async def test_handle_unknown_command_replies_with_hint():
    message = _message("/foo")
    await handle_unknown_command(message)
    message.answer.assert_awaited_once_with(UNKNOWN_COMMAND_TEXT)


async def test_handle_non_text_replies_with_hint():
    message = _message(text=None)
    await handle_non_text(message)
    message.answer.assert_awaited_once_with(NOT_TEXT_TEXT)
```

- [ ] **Step 2: Переконатися, що тести падають**

Run: `.venv/bin/python -m pytest tests/test_bot_handlers.py -q`
Expected: errors, `ModuleNotFoundError: No module named 'prophet_checker.bot.handlers'`

- [ ] **Step 3: Імплементація**

Створити `src/prophet_checker/bot/handlers.py`:

```python
from __future__ import annotations

import logging

from aiogram.types import Message

from prophet_checker.bot.texts import (
    NOT_TEXT_TEXT,
    START_TEXT,
    UNKNOWN_COMMAND_TEXT,
)

logger = logging.getLogger(__name__)


async def handle_start(message: Message) -> None:
    await message.answer(START_TEXT)


async def handle_unknown_command(message: Message) -> None:
    await message.answer(UNKNOWN_COMMAND_TEXT)


async def handle_non_text(message: Message) -> None:
    await message.answer(NOT_TEXT_TEXT)
```

- [ ] **Step 4: Тести зелені**

Run: `.venv/bin/python -m pytest tests/test_bot_handlers.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check src/prophet_checker/bot tests/test_bot_handlers.py
git add src/prophet_checker/bot/handlers.py tests/test_bot_handlers.py
git commit -m "feat(bot): хендлери /start, невідомої команди і не-тексту"
```

**Квіз (самоперевірка перед виконанням)**

1. Чому в тестах повідомлення — `MagicMock`, а не справжній aiogram `Message`?
   - А) aiogram Message неможливо створити в тесті
   - +Б) Хендлери — чисті функції; справжній `Message` — frozen pydantic, його `answer()` вимагає прив'язаного бота й мережі. Мок із `answer=AsyncMock` тестує рівно наш контракт
   - В) Так швидше пишеться, але гірше — варто переробити
2. `/help` окремого хендлера не має. Де він обробляється?
   - А) aiogram обробляє /help автоматично
   - +Б) У Task 3 `build_router` реєструє `handle_start` на `Command("start", "help")` — обидві команди відповідають `START_TEXT` (design §7)
   - В) /help поза скоупом v1

<details><summary>Відповіді</summary>

1. **Б** — репо-патерн: мокати boundary-об'єкти (як `_llm()` у `test_answer_orchestrator.py`).
2. **Б** — одна реєстрація на дві команди; порядок роутингу — Task 3.

</details>

---

### Task 3: `handle_question` + `build_router`

**Скоуп:** головний хендлер (design §6: typing → `answer()` → reply з обрізанням; §8: broad except як boundary; §9: логування) і роутер з порядком матчингу з design §5. Це найбільший таск — серце бота.

**Files:**
- Modify: `src/prophet_checker/bot/handlers.py`
- Test: `tests/test_bot_handlers.py` (дописати)

- [ ] **Step 1: Написати failing-тести**

Дописати в `tests/test_bot_handlers.py` (імпорти — замінити блок імпортів на цей):

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from prophet_checker.bot.handlers import (
    build_router,
    handle_non_text,
    handle_question,
    handle_start,
    handle_unknown_command,
)
from prophet_checker.bot.texts import (
    ERROR_TEXT,
    NOT_TEXT_TEXT,
    START_TEXT,
    TELEGRAM_MESSAGE_LIMIT,
    UNKNOWN_COMMAND_TEXT,
)
from prophet_checker.models.domain import AnswerResult
```

і додати в кінець файлу:

```python
def _orchestrator(answer_text="відповідь"):
    orch = MagicMock()
    orch.answer = AsyncMock(
        return_value=AnswerResult(query="q", answer=answer_text, sources=[])
    )
    return orch


# --- handle_question ---


async def test_question_replies_with_answer():
    message = _message("що казав про Крим?")
    orch = _orchestrator("прогноз справдився")

    await handle_question(message, orch)

    orch.answer.assert_awaited_once_with("що казав про Крим?")
    message.answer.assert_awaited_once_with("прогноз справдився")


async def test_question_sends_typing_before_answering():
    calls = []
    message = _message()
    message.bot.send_chat_action = AsyncMock(
        side_effect=lambda **_: calls.append("typing")
    )

    async def _answer(_question):
        calls.append("answer")
        return AnswerResult(query="q", answer="a", sources=[])

    orch = MagicMock()
    orch.answer = AsyncMock(side_effect=_answer)

    await handle_question(message, orch)

    assert calls == ["typing", "answer"]


@pytest.mark.parametrize("text", ["   ", None])
async def test_question_ignores_blank_text(text):
    message = _message(text)
    orch = _orchestrator()

    await handle_question(message, orch)

    orch.answer.assert_not_awaited()
    message.answer.assert_not_awaited()


async def test_question_truncates_long_answer():
    message = _message()
    orch = _orchestrator("а" * (TELEGRAM_MESSAGE_LIMIT + 500))

    await handle_question(message, orch)

    sent = message.answer.call_args.args[0]
    assert len(sent) == TELEGRAM_MESSAGE_LIMIT
    assert sent.endswith("…")


async def test_question_replies_with_error_text_on_failure():
    message = _message()
    orch = MagicMock()
    orch.answer = AsyncMock(side_effect=RuntimeError("LLM down"))

    await handle_question(message, orch)

    message.answer.assert_awaited_once_with(ERROR_TEXT)


# --- build_router: порядок матчингу = контракт design §5 ---


def test_router_registers_handlers_in_design_order():
    router = build_router()
    callbacks = [h.callback for h in router.message.handlers]
    assert callbacks == [
        handle_start,
        handle_unknown_command,
        handle_question,
        handle_non_text,
    ]


def test_router_start_handler_also_serves_help():
    router = build_router()
    command_filter = router.message.handlers[0].filters[0].callback
    assert set(command_filter.commands) == {"start", "help"}
```

- [ ] **Step 2: Переконатися, що тести падають**

Run: `.venv/bin/python -m pytest tests/test_bot_handlers.py -q`
Expected: `ImportError: cannot import name 'build_router'`

- [ ] **Step 3: Імплементація**

У `src/prophet_checker/bot/handlers.py` — замінити вміст на:

```python
from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command
from aiogram.types import Message

from prophet_checker.bot.texts import (
    ERROR_TEXT,
    NOT_TEXT_TEXT,
    START_TEXT,
    UNKNOWN_COMMAND_TEXT,
    truncate_for_telegram,
)
from prophet_checker.query.answer_orchestrator import AnswerOrchestrator

logger = logging.getLogger(__name__)


async def handle_start(message: Message) -> None:
    await message.answer(START_TEXT)


async def handle_unknown_command(message: Message) -> None:
    await message.answer(UNKNOWN_COMMAND_TEXT)


async def handle_question(message: Message, answer_orchestrator: AnswerOrchestrator) -> None:
    if message.text is None or not message.text.strip():
        return
    user_id = message.from_user.id if message.from_user else 0
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    started = time.monotonic()
    try:
        result = await answer_orchestrator.answer(message.text)
    except Exception:
        logger.exception("bot answer failed (user_id=%s)", user_id)
        await message.answer(ERROR_TEXT)
        return
    logger.info(
        "bot answer served: user_id=%s question_len=%d elapsed=%.1fs",
        user_id,
        len(message.text),
        time.monotonic() - started,
    )
    logger.debug("bot question: %s", message.text)
    await message.answer(truncate_for_telegram(result.answer))


async def handle_non_text(message: Message) -> None:
    await message.answer(NOT_TEXT_TEXT)


def build_router() -> Router:
    """Порядок реєстрації = порядок матчингу: команди → «/...» → текст → решта."""
    router = Router()
    router.message.register(handle_start, Command("start", "help"))
    router.message.register(handle_unknown_command, F.text.startswith("/"))
    router.message.register(handle_question, F.text)
    router.message.register(handle_non_text)
    return router
```

Зверни увагу: відповіді шлються без `parse_mode` (plain text) — це свідоме
рішення design §6 крок 4, не додавай форматування.

- [ ] **Step 4: Тести зелені**

Run: `.venv/bin/python -m pytest tests/test_bot_handlers.py -q`
Expected: `11 passed` (3 з Task 2 + 8 нових; parametrize дає 2 кейси)

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check src/prophet_checker/bot tests/test_bot_handlers.py
git add src/prophet_checker/bot/handlers.py tests/test_bot_handlers.py
git commit -m "feat(bot): Q&A-хендлер вільного тексту і роутер"
```

**Квіз (самоперевірка перед виконанням)**

1. Чому на порожній/пробільний текст бот мовчить, а не відповідає підказкою?
   - А) Недогляд — треба відповідати
   - +Б) Рішення design §7: не витрачати LLM-виклик і не шуміти; Telegram і так майже не дає надіслати порожнє
   - В) aiogram не пропускає такі повідомлення
2. Чому broad `except Exception` тут прийнятний, хоча зазвичай це анти-патерн?
   - А) Він тимчасовий, до появи типізованих помилок
   - +Б) Хендлер — process boundary (як HTTP-ендпоінти в `app.py`): останній рубіж, де збій перетворюється на `ERROR_TEXT` + `logger.exception`, інакше впаде обробка update
   - В) aiogram вимагає ловити все
3. Чому `handle_non_text` реєструється без фільтра?
   - А) Він обробляє і текст теж
   - +Б) Catch-all: усе, що не зматчилось вище (стікери/фото/войс), падає в нього; порядок реєстрації це гарантує
   - В) Фільтр F.sticker було б правильніше

<details><summary>Відповіді</summary>

1. **Б** — рядок «Порожній / пробільний текст → ігнорувати (без LLM-виклику)».
2. **Б** — той самий патерн, що `run_ingestion`/`answer` у `app.py`.
3. **Б** — тест на порядок реєстрації фіксує саме це.

</details>

---

### Task 4: `bot/runner.py` — `build_bot_runner` + `BotRunner`

**Скоуп:** збірка aiogram `Bot`+`Dispatcher` (DI оркестратора через workflow_data, design §5) і життєвий цикл polling-таски: старт з `handle_signals=False`, graceful stop, `CRITICAL`-лог при смерті таски (design §8).

**Files:**
- Create: `src/prophet_checker/bot/runner.py`
- Test: `tests/test_bot_runner.py` (новий)

- [ ] **Step 1: Написати failing-тести**

Створити `tests/test_bot_runner.py`:

```python
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from prophet_checker.bot.runner import BotRunner, build_bot_runner


def _fake_dispatcher():
    dispatcher = MagicMock()
    dispatcher.start_polling = AsyncMock()
    dispatcher.stop_polling = AsyncMock()
    return dispatcher


def _fake_bot():
    bot = MagicMock()
    bot.session.close = AsyncMock()
    return bot


# --- build_bot_runner ---


def test_build_bot_runner_wires_orchestrator_into_dispatcher():
    orch = MagicMock()
    runner = build_bot_runner("123456:TEST-TOKEN", orch)
    assert runner.dispatcher["answer_orchestrator"] is orch


# --- BotRunner lifecycle ---


async def test_stop_before_start_is_noop():
    bot = _fake_bot()
    runner = BotRunner(bot, _fake_dispatcher())

    await runner.stop()

    bot.session.close.assert_not_awaited()


async def test_start_then_stop_shuts_down_cleanly():
    bot = _fake_bot()
    dispatcher = _fake_dispatcher()
    runner = BotRunner(bot, dispatcher)

    await runner.start()
    await runner.stop()

    dispatcher.start_polling.assert_awaited_once_with(bot, handle_signals=False)
    dispatcher.stop_polling.assert_awaited_once()
    bot.session.close.assert_awaited_once()


async def test_second_stop_is_noop():
    bot = _fake_bot()
    runner = BotRunner(bot, _fake_dispatcher())

    await runner.start()
    await runner.stop()
    await runner.stop()

    bot.session.close.assert_awaited_once()


async def test_crashed_polling_is_logged_critical(caplog):
    bot = _fake_bot()
    dispatcher = _fake_dispatcher()
    dispatcher.start_polling = AsyncMock(side_effect=RuntimeError("boom"))
    runner = BotRunner(bot, dispatcher)

    with caplog.at_level(logging.CRITICAL):
        await runner.start()
        await asyncio.sleep(0)  # перший цикл: таска виконується і падає
        await asyncio.sleep(0)  # другий цикл: done-callback логує

    assert any("polling task died" in r.getMessage() for r in caplog.records)
    await runner.stop()
```

- [ ] **Step 2: Переконатися, що тести падають**

Run: `.venv/bin/python -m pytest tests/test_bot_runner.py -q`
Expected: errors, `ModuleNotFoundError: No module named 'prophet_checker.bot.runner'`

- [ ] **Step 3: Імплементація**

Створити `src/prophet_checker/bot/runner.py`:

```python
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher

from prophet_checker.bot.handlers import build_router
from prophet_checker.query.answer_orchestrator import AnswerOrchestrator

logger = logging.getLogger(__name__)


def _log_if_crashed(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical("bot polling task died: %s", exc, exc_info=exc)


class BotRunner:
    """Життєвий цикл long-polling: старт таскою, graceful stop, закриття сесії."""

    def __init__(self, bot: Bot, dispatcher: Dispatcher) -> None:
        self.bot = bot
        self.dispatcher = dispatcher
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        # handle_signals=False: сигналами володіє uvicorn, не aiogram
        self._task = asyncio.create_task(
            self.dispatcher.start_polling(self.bot, handle_signals=False)
        )
        self._task.add_done_callback(_log_if_crashed)

    async def stop(self) -> None:
        if self._task is None:
            return
        # stop_polling кидає RuntimeError, якщо polling ще/вже не крутиться
        with suppress(RuntimeError):
            await self.dispatcher.stop_polling()
        # падіння таски вже залоговане done-callback'ом — на shutdown не перекидаємо
        with suppress(Exception):
            await self._task
        self._task = None
        await self.bot.session.close()


def build_bot_runner(token: str, answer_orchestrator: AnswerOrchestrator) -> BotRunner:
    bot = Bot(token=token)
    dispatcher = Dispatcher(answer_orchestrator=answer_orchestrator)
    dispatcher.include_router(build_router())
    return BotRunner(bot, dispatcher)
```

- [ ] **Step 4: Тести зелені**

Run: `.venv/bin/python -m pytest tests/test_bot_runner.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check src/prophet_checker/bot tests/test_bot_runner.py
git add src/prophet_checker/bot/runner.py tests/test_bot_runner.py
git commit -m "feat(bot): BotRunner — старт/стоп long-polling"
```

**Квіз (самоперевірка перед виконанням)**

1. Навіщо `handle_signals=False` при `start_polling`?
   - А) Оптимізація — сигнали сповільнюють polling
   - +Б) aiogram за замовчуванням вішає свої SIGINT/SIGTERM-хендлери; у нашому процесі сигналами володіє uvicorn — подвійні хендлери зламали б graceful shutdown
   - В) Без цього polling не стартує в asyncio-тасці
2. Чому в `stop()` помилка з `await self._task` глушиться, а не перекидається?
   - А) Помилки в тасках не важливі
   - +Б) Якщо таска померла — це вже залоговано `CRITICAL` done-callback'ом; на shutdown повторний raise лише зашумить вихід застосунку
   - В) asyncio забороняє raise у teardown
3. Токен у тесті — `"123456:TEST-TOKEN"`. Чому не просто `"test"`?
   - А) Випадковий вибір
   - +Б) aiogram валідує формат токена (`digits:tail`) у конструкторі `Bot`; невалідний рядок кинув би `TokenValidationError` ще до нашого коду
   - В) Це справжній тестовий токен Telegram

<details><summary>Відповіді</summary>

1. **Б** — design §5 (wiring-булет).
2. **Б** — design §8: «Polling-таска померла → CRITICAL у лог; API живе далі».
3. **Б** — мережі при цьому немає: конструктор лише валідує рядок.

</details>

---

### Task 5: конфіг `bot_enabled` + `factory.build_bot` + `.env.example`

**Скоуп:** одне нове поле в `Settings` і збірка бота в composition root: вимкнено → `None`; увімкнено без токена → fail fast; увімкнено з токеном → `BotRunner` зі `stop()` в `AsyncExitStack` (design §5 wiring, §8 останній рядок). Поле `bot_enabled` — чиста field-декларація, окремо не тестується (репо-правило); поведінку тестує `build_bot`.

**Files:**
- Modify: `src/prophet_checker/config.py:11` (після `telegram_bot_token`)
- Modify: `src/prophet_checker/factory.py` (імпорти + нова функція в кінці)
- Modify: `.env.example` (Telegram-секція)
- Test: `tests/test_factory.py` (дописати)

- [ ] **Step 1: Написати failing-тести**

У `tests/test_factory.py` — до наявних імпортів додати:

```python
from prophet_checker.bot.runner import BotRunner
from prophet_checker.factory import build_bot
```

(рядок `from prophet_checker.factory import build_orchestrator` лишити; `pytest`,
`AsyncMock`, `MagicMock`, `patch`, `AsyncExitStack`, `Settings` там уже імпортовані)

і додати в кінець файлу:

```python
# --- build_bot ---


async def test_build_bot_disabled_returns_none():
    settings = Settings(bot_enabled=False)
    async with AsyncExitStack() as stack:
        assert await build_bot(settings, stack, MagicMock()) is None


async def test_build_bot_enabled_without_token_fails_fast():
    settings = Settings(bot_enabled=True, telegram_bot_token="")
    async with AsyncExitStack() as stack:
        with pytest.raises(ValueError, match="telegram_bot_token"):
            await build_bot(settings, stack, MagicMock())


async def test_build_bot_registers_stop_on_stack():
    settings = Settings(bot_enabled=True, telegram_bot_token="123456:TEST-TOKEN")
    fake_runner = MagicMock(spec=BotRunner)
    fake_runner.stop = AsyncMock()

    with patch("prophet_checker.factory.build_bot_runner", return_value=fake_runner):
        async with AsyncExitStack() as stack:
            runner = await build_bot(settings, stack, MagicMock())
            assert runner is fake_runner

    fake_runner.stop.assert_awaited_once()
```

- [ ] **Step 2: Переконатися, що тести падають**

Run: `.venv/bin/python -m pytest tests/test_factory.py -q`
Expected: `ImportError: cannot import name 'build_bot'`

- [ ] **Step 3: Імплементація**

`src/prophet_checker/config.py` — після рядка `telegram_bot_token: str = ""` додати:

```python
    bot_enabled: bool = False  # вмикає Telegram-бота (long-polling у процесі API)
```

`src/prophet_checker/factory.py` — до імпортів додати:

```python
from prophet_checker.bot.runner import BotRunner, build_bot_runner
```

і в кінець файлу:

```python
async def build_bot(
    settings: Settings, stack: AsyncExitStack, answer_orchestrator: AnswerOrchestrator
) -> BotRunner | None:
    if not settings.bot_enabled:
        return None
    if not settings.telegram_bot_token:
        raise ValueError("bot_enabled=True, але telegram_bot_token порожній")
    runner = build_bot_runner(settings.telegram_bot_token, answer_orchestrator)
    stack.push_async_callback(runner.stop)
    return runner
```

`.env.example` — секцію Telegram переробити так (токен переїжджає в окрему
бот-секцію; `TELEGRAM_API_ID/HASH/TG_SESSION_PATH` — колекторські, лишаються):

```
# -- Telegram (data collection, Telethon user-сесія) --
TELEGRAM_API_ID=your-api-id
TELEGRAM_API_HASH=your-api-hash
TG_SESSION_PATH=tg_session

# -- Telegram bot (user-facing Q&A; токен від BotFather) --
TELEGRAM_BOT_TOKEN=your-bot-token-here
BOT_ENABLED=false
```

- [ ] **Step 4: Тести зелені**

Run: `.venv/bin/python -m pytest tests/test_factory.py tests/test_config.py -q`
Expected: `PASS` (усі наявні + 3 нові)

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff check src tests
git add src/prophet_checker/config.py src/prophet_checker/factory.py .env.example tests/test_factory.py
git commit -m "feat(bot): конфіг bot_enabled і збірка бота в composition root"
```

**Квіз (самоперевірка перед виконанням)**

1. Чому fail-fast на відсутній токен живе у `build_bot`, а не validator-ом у `Settings`?
   - А) У pydantic-settings немає validator-ів
   - +Б) Умова крос-польова і стосується лише збірки бота: `Settings` з вимкненим ботом (та всі eval-скрипти) мусять конструюватися без токена; ламатися має саме спроба зібрати бота
   - В) Validator сповільнив би старт
2. Чому в `test_build_bot_registers_stop_on_stack` патчиться `prophet_checker.factory.build_bot_runner`, а не `prophet_checker.bot.runner.build_bot_runner`?
   - А) Однаково — обидва шляхи працюють
   - +Б) Патчити треба ім'я там, де його шукають: `factory` імпортує функцію у свій неймспейс, і виклик іде через `factory.build_bot_runner`
   - В) Модуль runner захищений від патчингу
3. Що станеться зі `stop()`, якщо старт застосунку впаде ПІСЛЯ `build_bot`, але до `start()`?
   - А) Витік aiohttp-сесії
   - +Б) `AsyncExitStack` розмотається і викличе `stop()`; той побачить `_task is None` і тихо вийде (Task 4, «stop before start is noop»)
   - В) Впаде RuntimeError у teardown

<details><summary>Відповіді</summary>

1. **Б** — той самий принцип, що «fail fast у composition root» (design §8).
2. **Б** — класика `unittest.mock`: patch where it's looked up; точно як `patch("prophet_checker.factory.TelegramClient")` у наявних тестах.
3. **Б** — саме тому реєстрація в стеку йде одразу після збірки, а no-op-stop критичний.

</details>

---

### Task 6: wiring у `app.py` lifespan

**Скоуп:** три рядки в lifespan — зібрати бота після `answer_orchestrator` і стартанути, якщо він є (design §5 wiring). Юніт-тесту немає: app-тести ганяють `ASGITransport` без lifespan, а реальний lifespan тягне БД/Telethon — покривається смоуком (Task 7).

**Files:**
- Modify: `src/prophet_checker/app.py:10-14` (імпорт) і `:21-29` (lifespan)

- [ ] **Step 1: Імплементація**

У `src/prophet_checker/app.py` імпорт із factory доповнити `build_bot`:

```python
from prophet_checker.factory import (
    build_answer_orchestrator,
    build_bot,
    build_orchestrator,
    build_query_orchestrator,
)
```

У `lifespan` після рядка
`app.state.answer_orchestrator = await build_answer_orchestrator(settings, stack)`
і перед `yield` додати:

```python
        bot_runner = await build_bot(settings, stack, app.state.answer_orchestrator)
        if bot_runner is not None:
            await bot_runner.start()
```

- [ ] **Step 2: Уся сюїта зелена**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: `348 passed` (325 бейзлайн + 23 нові; звір із фактичним числом свого прогону)

- [ ] **Step 3: Commit**

```bash
.venv/bin/ruff check src tests
git add src/prophet_checker/app.py
git commit -m "feat(bot): старт бота в lifespan застосунку"
```

**Квіз (самоперевірка перед виконанням)**

1. Чому бот стартує в lifespan `app.py`, а не всередині `build_bot`?
   - А) Випадкове рішення
   - +Б) Composition root збирає і реєструє teardown; момент старту — відповідальність життєвого циклу застосунку. Розділення дозволяє зібрати бота і не стартувати (тести factory)
   - В) aiogram не дозволяє стартувати поза lifespan
2. Що буде з ботом при `BOT_ENABLED=false` (дефолт — локальний дев, CI)?
   - А) Стартує з порожнім токеном і падає
   - +Б) `build_bot` поверне `None`, `start()` не викличеться — процес поводиться точно як до цього треку
   - В) Треба ще виставити TELEGRAM_BOT_TOKEN=disabled

<details><summary>Відповіді</summary>

1. **Б** — той самий поділ, що в усіх `build_*`: factory збирає, lifespan володіє часом життя.
2. **Б** — дефолт вимкнено; жодних змін для наявних оточень.

</details>

---

### Task 7: runbook + індекси документації

**Скоуп:** рансетап-нотатка (BotFather → токен → смоук → прод), секція треку в `docs/README.md`, запис у `progress.md`. Коду немає — лише документація за фактом зробленого.

**Files:**
- Create: `runbook/bot.md`
- Modify: `docs/README.md` (нова секція після generation-блоку)
- Modify: `progress.md` (нотатка в `## Notes` + рядок у Phase 7)

- [ ] **Step 1: Створити `runbook/bot.md`**

```markdown
# Runbook — Telegram-бот

## Разово: створити бота

1. У Telegram → `@BotFather` → `/newbot` → ім'я і username бота.
2. Зберегти токен виду `123456789:AA...` (це секрет — тільки `.env`/S3).

## Локальний смоук

1. У `.env`: `TELEGRAM_BOT_TOKEN=<токен>`, `BOT_ENABLED=true`
   (+ робочі LLM/embedding-ключі та БД з даними — див. `first-ingest.md`).
2. `docker compose up -d && .venv/bin/alembic upgrade head`.
3. `.venv/bin/python -m prophet_checker`.
4. У Telegram: `/start` → бот описує себе і дає приклади питань.
5. Поставити питання з прикладів. Очікування: typing-індикатор,
   за кілька секунд відповідь за answer-contract (без UUID/enum-ів,
   з дисклеймером).
6. Питання поза корпусом («який курс біткоїна?») → текст відмови.
7. Надіслати стікер → «Я розумію лише текстові питання…».

## Прод (EC2-бокс)

1. Додати `TELEGRAM_BOT_TOKEN=<токен>` і `BOT_ENABLED=true` в env-файл
   секретів у приватному S3 (див. `docs/aws-deploy/`).
2. Перезапустити compose на боксі (потягне свіжі секрети).
3. Смоук пп. 4–7 з телефона. Бот живе, поки живе бокс — для білінгового
   бокса, який гасять, це очікувано.
```

- [ ] **Step 2: Секція в `docs/README.md`**

Після блоку про `generation/` додати:

```markdown
## 🤖 [`telegram-bot/`](telegram-bot/) — Telegram-бот (user-facing Q&A)

Остання миля продукту: тонкий фронтенд над `AnswerOrchestrator` — aiogram,
long-polling у процесі API. Stateless, author-agnostic, публічний без лімітів.

| Документ | Призначення |
|----------|-------------|
| [`2026-07-11-telegram-bot-design.md`](telegram-bot/2026-07-11-telegram-bot-design.md) | Spec: рішення й чому, компоненти, UX-таблиця, помилки |
| [`2026-07-11-telegram-bot-plan.md`](telegram-bot/2026-07-11-telegram-bot-plan.md) | Implementation plan — 7 тасків TDD |
```

- [ ] **Step 3: Запис у `progress.md`**

У `## Phase 7: Future (post-MVP)` пункт `3. Telegram bot frontend + RAG query endpoint.`
замінити на:

```markdown
3. ~~Telegram bot frontend~~ → зроблено (2026-07-11, `docs/telegram-bot/`); RAG query endpoint був готовий раніше.
```

У `## Notes` додати в кінець:

```markdown
- **Telegram-бот v1 (2026-07-11):** остання миля продукту — тонкий Q&A-фронтенд над `AnswerOrchestrator`: пакет `bot/` (texts/handlers/runner), aiogram long-polling asyncio-таскою в lifespan FastAPI (webhook неможливий — бокс SSH-only), stateless, author-agnostic, публічний без лімітів. Конфіг: нове поле `bot_enabled` (`telegram_bot_token` і aiogram були зарезервовані при скафолді); fail-fast без токена; `build_bot` у composition root, teardown через `AsyncExitStack`; смерть polling-таски → CRITICAL, API живе. +23 тести (сюїта 348 — звір з фактичним прогоном). Runbook: `runbook/bot.md`. Смоук на живому токені — за користувачем. Design+plan: `docs/telegram-bot/`.
```

- [ ] **Step 4: Commit**

```bash
git add runbook/bot.md docs/README.md progress.md
git commit -m "docs(bot): runbook, індекс доків, progress"
```

**Квіз (самоперевірка перед виконанням)**

1. Чому смоук на живому боті — «за користувачем», а не таск плану?
   - А) Смоук не потрібен
   - +Б) Потрібні реальний BotFather-токен, LLM-ключі та БД з даними — їх немає в сесії виконавця; патерн уже усталений (aws-deploy: «box-деплой за користувачем»)
   - В) Смоук замінюється юніт-тестами

<details><summary>Відповіді</summary>

1. **Б** — креденшели й дані живуть тільки в юзера; план дає точний чекліст у runbook.

</details>

---

### Task 8: фінальна верифікація

**Скоуп:** повний прогін гейтів перед завершенням гілки (verification-before-completion). Жодних нових змін — тільки перевірки.

- [ ] **Step 1: Уся сюїта**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: `348 passed` (звір з фактичним числом; якщо інше — розібратись ДО завершення)

- [ ] **Step 2: Лінт і формат**

Run: `.venv/bin/ruff check src tests && .venv/bin/ruff format --check src/prophet_checker/bot tests/test_bot_texts.py tests/test_bot_handlers.py tests/test_bot_runner.py`
Expected: без помилок (68 legacy-помилок поза `src/prophet_checker/bot` — відомий борг, не чіпати)

- [ ] **Step 3: Cognitive complexity**

Run: `.venv/bin/complexipy src/prophet_checker/bot`
Expected: усі функції ≤ 12 (реально ≤ 5)

- [ ] **Step 4: Історія комітів охайна**

Run: `git log --oneline main..HEAD`
Expected: 3 docs-коміти (design ×2 + plan) + 7 комітів тасків, кожен атомарний

Після цього гілка готова до фінішу (merge/PR — окремим рішенням користувача,
skill `finishing-a-development-branch`); живий смоук — `runbook/bot.md`.
