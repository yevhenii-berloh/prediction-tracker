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


def test_build_bot_runner_wires_dependencies_into_dispatcher():
    orch = MagicMock()
    repo = MagicMock()
    runner = build_bot_runner("123456:TEST-TOKEN", orch, repo)
    assert runner.dispatcher["answer_orchestrator"] is orch
    assert runner.dispatcher["query_log_repo"] is repo


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
