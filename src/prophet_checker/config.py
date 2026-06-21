from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://prophet:prophet@localhost:5432/prophet_checker"
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    gemini_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: str = ""
    embeddings_enabled: bool = True
    tg_session_path: str = "tg_session"
    verification_confidence_threshold: float = 0.6

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # allow eval-only vars (ANTHROPIC_API_KEY etc.) without schema bloat
    }


def get_settings() -> Settings:
    return Settings()
