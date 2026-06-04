from __future__ import annotations

from contextlib import AsyncExitStack

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from telethon import TelegramClient

from prophet_checker.analysis.extractor import PredictionExtractor
from prophet_checker.analysis.verifier import Verifier
from prophet_checker.config import Settings
from prophet_checker.ingestion import IngestionOrchestrator
from prophet_checker.llm import EmbeddingClient, LLMClient
from prophet_checker.models.domain import SourceType
from prophet_checker.sources.telegram import TelegramSource
from prophet_checker.storage.postgres import (
    PostgresPredictionRepository,
    PostgresSourceRepository,
)
from prophet_checker.verification import VerificationOrchestrator


async def build_orchestrator(
    settings: Settings, stack: AsyncExitStack
) -> IngestionOrchestrator:
    engine = create_async_engine(settings.database_url, echo=False)
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
    engine = create_async_engine(settings.database_url, echo=False)
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
