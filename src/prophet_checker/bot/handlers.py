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
