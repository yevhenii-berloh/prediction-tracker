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
    compose_answer_message,
)
from prophet_checker.query.answer_orchestrator import AnswerOrchestrator
from prophet_checker.storage.interfaces import QueryLogRepository

logger = logging.getLogger(__name__)


async def handle_start(message: Message) -> None:
    await message.answer(START_TEXT)


async def handle_unknown_command(message: Message) -> None:
    await message.answer(UNKNOWN_COMMAND_TEXT)


async def _record_query(
    repo: QueryLogRepository,
    user_id: int,
    question: str,
    answer: str | None,
    latency_ms: int,
) -> None:
    """Свій try/except: збій моніторингу не має права зламати відповідь юзеру."""
    try:
        await repo.save(
            user_id=user_id,
            question=question,
            answer=answer,
            latency_ms=latency_ms,
        )
    except Exception:
        logger.exception("query log write failed (user_id=%s)", user_id)


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
        elapsed = time.monotonic() - started
        logger.exception("bot answer failed (user_id=%s)", user_id)
        await _record_query(query_log_repo, user_id, message.text, None, int(elapsed * 1000))
        await message.answer(ERROR_TEXT)
        return
    # один вимір на обидва числа: метрика не роздуває себе власним записом у БД
    elapsed = time.monotonic() - started
    # пишемо сиру відповідь моделі, а не підрізане під ліміт Telegram повідомлення
    await _record_query(query_log_repo, user_id, message.text, result.answer, int(elapsed * 1000))
    logger.info(
        "bot answer served: user_id=%s question_len=%d elapsed=%.1fs",
        user_id,
        len(message.text),
        elapsed,
    )
    logger.debug("bot question: %s", message.text)
    await message.answer(
        compose_answer_message(result.answer, result.citations), parse_mode="HTML"
    )


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
