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
from prophet_checker.sources.telegram import TelegramSource
from prophet_checker.storage.engine import make_engine
from prophet_checker.storage.postgres import (
    PostgresPredictionRepository,
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

    tg_client = TelegramClient(
        session=settings.tg_session_path,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    await tg_client.start()
    stack.push_async_callback(tg_client.disconnect)
    telegram_source = TelegramSource(tg_client)

    return IngestionOrchestrator(
        session_factory=session_factory,
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=extractor,
        embedder=embedder,
        sources={SourceType.TELEGRAM: telegram_source},
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

    return QueryOrchestrator(
        embedder, vector_store, prediction_repo, relevance_threshold=settings.relevance_threshold
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
    return AnswerOrchestrator(llm, query_orchestrator)


async def build_bot(
    settings: Settings, stack: AsyncExitStack, answer_orchestrator: AnswerOrchestrator
) -> BotRunner | None:
    if not settings.bot_enabled:
        return None
    if not settings.telegram_bot_token:
        raise ValueError("bot_enabled=True, але telegram_bot_token порожній")
    runner = build_bot_runner(settings.telegram_bot_token, answer_orchestrator)
    stack.push_async_callback(runner.stop)
    return runner
