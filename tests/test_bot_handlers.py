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
