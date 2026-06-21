"""Backfill ембедингів для наявних прогнозів (claim+situation). Разовий ops-скрипт:
пройтись по всіх прогнозах у БД, заембедити claim+situation, записати у VectorStore.
Без нього пошук бачитиме лише прогнози, заінджещені ПІСЛЯ embeddings_enabled=True.

Передумова: docker compose up -d; alembic upgrade head; OPENAI_API_KEY у .env.

Usage:
    .venv/bin/python scripts/ingestion/backfill_embeddings.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from contextlib import AsyncExitStack  # noqa: E402

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from prophet_checker.analysis.embedding_text import embedding_text  # noqa: E402
from prophet_checker.config import Settings  # noqa: E402
from prophet_checker.llm import EmbeddingClient  # noqa: E402
from prophet_checker.storage.postgres import (  # noqa: E402
    PostgresPersonRepository,
    PostgresPredictionRepository,
    PostgresVectorStore,
)


async def backfill(person_repo, prediction_repo, vector_store, embedder) -> int:
    """Для кожного прогнозу всіх осіб: embed(claim+situation) → store_embedding. Повертає к-сть."""
    count = 0
    for person in await person_repo.list_all():
        for pred in await prediction_repo.get_by_person(person.id):
            vector = await embedder.embed(embedding_text(pred))
            await vector_store.store_embedding(pred.id, vector)
            count += 1
    return count


async def run() -> None:
    settings = Settings()
    embedder = EmbeddingClient(model=settings.embedding_model, api_key=settings.openai_api_key)
    async with AsyncExitStack() as stack:
        engine = create_async_engine(settings.database_url, echo=False)
        stack.push_async_callback(engine.dispose)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        n = await backfill(
            PostgresPersonRepository(sf),
            PostgresPredictionRepository(sf),
            PostgresVectorStore(sf),
            embedder,
        )
    print(f"backfilled embeddings: {n} predictions")


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    asyncio.run(run())


if __name__ == "__main__":
    main()
