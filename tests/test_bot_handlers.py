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
