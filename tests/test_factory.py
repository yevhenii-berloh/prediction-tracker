from __future__ import annotations

from contextlib import AsyncExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prophet_checker.bot.runner import BotRunner
from prophet_checker.config import Settings
from prophet_checker.factory import build_bot
from prophet_checker.factory import build_orchestrator
from prophet_checker.ingestion import IngestionOrchestrator


def _settings_with_test_env(monkeypatch) -> Settings:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:x@localhost:5432/x")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "test-hash")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("TG_SESSION_PATH", "/tmp/test_session")
    monkeypatch.setenv("TELEGRAM_SOURCE_ENABLED", "true")
    return Settings()


async def test_build_orchestrator_returns_orchestrator(monkeypatch):
    settings = _settings_with_test_env(monkeypatch)

    with patch("prophet_checker.factory.TelegramClient") as MockTg:
        mock_tg_instance = MockTg.return_value
        mock_tg_instance.start = AsyncMock()
        mock_tg_instance.disconnect = AsyncMock()

        async with AsyncExitStack() as stack:
            orchestrator = await build_orchestrator(settings, stack)
            assert isinstance(orchestrator, IngestionOrchestrator)


async def test_build_orchestrator_registers_cleanup(monkeypatch):
    settings = _settings_with_test_env(monkeypatch)

    with patch("prophet_checker.factory.TelegramClient") as MockTg:
        mock_tg_instance = MockTg.return_value
        mock_tg_instance.start = AsyncMock()
        mock_tg_instance.disconnect = AsyncMock()

        async with AsyncExitStack() as stack:
            await build_orchestrator(settings, stack)

        mock_tg_instance.disconnect.assert_called_once()


async def test_build_orchestrator_skips_telegram_when_source_disabled(monkeypatch):
    settings = _settings_with_test_env(monkeypatch).model_copy(
        update={"telegram_source_enabled": False}
    )

    with patch("prophet_checker.factory.TelegramClient") as MockTg:
        mock_tg_instance = MockTg.return_value
        mock_tg_instance.start = AsyncMock()
        mock_tg_instance.disconnect = AsyncMock()

        async with AsyncExitStack() as stack:
            orchestrator = await build_orchestrator(settings, stack)

    MockTg.assert_not_called()
    assert isinstance(orchestrator, IngestionOrchestrator)


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
