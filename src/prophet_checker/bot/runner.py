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
