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
