from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://prophet:prophet@localhost:5432/prophet_checker"
    db_ssl_mode: str = (
        "disable"  # disable | require | verify-full; require на RDS (rds.force_ssl=1)
    )
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    gemini_api_key: str = ""
    telegram_bot_token: str = ""
    bot_enabled: bool = False  # вмикає Telegram-бота (long-polling у процесі API)
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: str = ""
    embeddings_enabled: bool = True
    tg_session_path: str = "tg_session"
    telegram_source_enabled: bool = True  # False = локальний запуск без Telethon user-сесії (щоб не ділити auth-key з деплоєм → AuthKeyDuplicatedError)
    verification_confidence_threshold: float = 0.6
    relevance_threshold: float | None = (
        None  # None = top-k без порога; ставимо після sweep (задача A)
    )
    query_planner_enabled: bool = True  # False = аварійний обхід: пошук без фільтрів (design Р4)
    log_level: str = "INFO"
    app_host: str = "127.0.0.1"  # 0.0.0.0 у контейнері (compose), інакше застосунок недосяжний ззовні контейнера

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # allow eval-only vars (ANTHROPIC_API_KEY etc.) without schema bloat
    }


def get_settings() -> Settings:
    return Settings()
