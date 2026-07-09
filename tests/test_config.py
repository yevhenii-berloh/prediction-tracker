from prophet_checker.config import Settings


def test_settings_from_env(env_vars):
    settings = Settings()
    assert settings.database_url == "postgresql+asyncpg://test:test@localhost:5432/test_db"
    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.llm_api_key == "sk-test-key"
    assert settings.telegram_bot_token == "test-bot-token"


def test_settings_defaults():
    settings = Settings(
        llm_api_key="key",
        telegram_bot_token="token",
        telegram_api_id=1,
        telegram_api_hash="hash",
    )
    assert settings.database_url == "postgresql+asyncpg://prophet:prophet@localhost:5432/prophet_checker"
    assert settings.verification_confidence_threshold == 0.6


def test_settings_includes_fastapi_fields(env_vars):
    settings = Settings()
    assert settings.openai_api_key == "sk-test-openai-key"
    assert settings.tg_session_path == "/tmp/test_session"


def test_settings_app_host_default():
    settings = Settings(
        llm_api_key="key",
        telegram_bot_token="token",
        telegram_api_id=1,
        telegram_api_hash="hash",
    )
    assert settings.app_host == "127.0.0.1"


def test_settings_app_host_from_env(monkeypatch):
    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    settings = Settings(
        llm_api_key="key",
        telegram_bot_token="token",
        telegram_api_id=1,
        telegram_api_hash="hash",
    )
    assert settings.app_host == "0.0.0.0"
