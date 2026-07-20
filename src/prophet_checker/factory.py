from __future__ import annotations

from contextlib import AsyncExitStack

from sqlalchemy.ext.asyncio import async_sessionmaker
from telethon import TelegramClient

from prophet_checker.analysis.extractor import PredictionExtractor
from prophet_checker.analysis.verifier import Verifier
from prophet_checker.bot.runner import BotRunner, build_bot_runner
from prophet_checker.config import Settings
from prophet_checker.ingestion import IngestionOrchestrator
from prophet_checker.llm import EmbeddingClient, LLMClient
from prophet_checker.models.domain import SourceType
from prophet_checker.query import QueryOrchestrator
from prophet_checker.query.answer_orchestrator import AnswerOrchestrator
from prophet_checker.query.planner import QueryPlanner
from prophet_checker.sources.base import Source
from prophet_checker.sources.telegram import TelegramSource
from prophet_checker.storage.engine import make_engine
from prophet_checker.storage.postgres import (
    PostgresPersonRepository,
    PostgresPredictionRepository,
    PostgresQueryLogRepository,
    PostgresSourceRepository,
    PostgresVectorStore,
)
from prophet_checker.verification import VerificationOrchestrator


async def build_orchestrator(settings: Settings, stack: AsyncExitStack) -> IngestionOrchestrator:
    engine = make_engine(settings.database_url, settings.db_ssl_mode)
    stack.push_async_callback(engine.dispose)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    source_repo = PostgresSourceRepository(session_factory)
    prediction_repo = PostgresPredictionRepository(session_factory)

    llm = LLMClient(
        provider=settings.llm_provider,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
    )
    embedder = None
    if settings.embeddings_enabled:
        embedder = EmbeddingClient(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
        )
    extractor = PredictionExtractor(llm)

    sources: dict[SourceType, Source] = {}
    if settings.telegram_source_enabled:
        tg_client = TelegramClient(
            session=settings.tg_session_path,
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
        )
        await tg_client.start()
        stack.push_async_callback(tg_client.disconnect)
        sources[SourceType.TELEGRAM] = TelegramSource(tg_client)

    return IngestionOrchestrator(
        session_factory=session_factory,
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=extractor,
        embedder=embedder,
        sources=sources,
    )


async def build_verification_orchestrator(
    settings: Settings, stack: AsyncExitStack
) -> VerificationOrchestrator:
    engine = make_engine(settings.database_url, settings.db_ssl_mode)
    stack.push_async_callback(engine.dispose)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    prediction_repo = PostgresPredictionRepository(session_factory)
    llm = LLMClient(
        provider="gemini",
        model="gemini-3.1-flash-lite-preview",
        api_key=settings.gemini_api_key,
    )
    verifier = Verifier(llm)

    return VerificationOrchestrator(prediction_repo, verifier)


async def build_query_orchestrator(settings: Settings, stack: AsyncExitStack) -> QueryOrchestrator:
    engine = make_engine(settings.database_url, settings.db_ssl_mode)
    stack.push_async_callback(engine.dispose)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    prediction_repo = PostgresPredictionRepository(session_factory)
    vector_store = PostgresVectorStore(session_factory)
    embedder = EmbeddingClient(model=settings.embedding_model, api_key=settings.openai_api_key)

    planner = None
    if settings.query_planner_enabled:
        planner_llm = LLMClient(
            provider="gemini",
            model="gemini-3.1-flash-lite-preview",
            api_key=settings.gemini_api_key,
            temperature=0,
        )
        planner = QueryPlanner(planner_llm, PostgresPersonRepository(session_factory))

    return QueryOrchestrator(
        embedder,
        vector_store,
        prediction_repo,
        relevance_threshold=settings.relevance_threshold,
        planner=planner,
    )


async def build_answer_orchestrator(
    settings: Settings, stack: AsyncExitStack
) -> AnswerOrchestrator:
    query_orchestrator = await build_query_orchestrator(settings, stack)
    llm = LLMClient(
        provider="gemini",
        model="gemini-3.1-flash-lite-preview",
        api_key=settings.gemini_api_key,
        temperature=0,
    )
    # Власний engine, як і в сусідніх білдерах: build_query_orchestrator свій
    # session_factory назовні не віддає, а source_repo потрібен для цитат.
    engine = make_engine(settings.database_url, settings.db_ssl_mode)
    stack.push_async_callback(engine.dispose)
    source_repo = PostgresSourceRepository(async_sessionmaker(engine, expire_on_commit=False))

    return AnswerOrchestrator(
        llm,
        query_orchestrator,
        source_repo=source_repo,
        citations_enabled=settings.citations_enabled,
    )


async def build_bot(
    settings: Settings, stack: AsyncExitStack, answer_orchestrator: AnswerOrchestrator
) -> BotRunner | None:
    if not settings.bot_enabled:
        return None
    if not settings.telegram_bot_token:
        raise ValueError("bot_enabled=True, але telegram_bot_token порожній")
    # engine будується після guard-ів: при вимкненому боті зайвого конекту до БД нема
    engine = make_engine(settings.database_url, settings.db_ssl_mode)
    stack.push_async_callback(engine.dispose)
    query_log_repo = PostgresQueryLogRepository(
        async_sessionmaker(engine, expire_on_commit=False)
    )
    runner = build_bot_runner(settings.telegram_bot_token, answer_orchestrator, query_log_repo)
    stack.push_async_callback(runner.stop)
    return runner
