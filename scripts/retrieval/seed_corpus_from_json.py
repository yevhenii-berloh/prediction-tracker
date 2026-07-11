"""Seed local dev Postgres з експортованого корпусу (scripts/data/retrieval/corpus.json).

corpus.json — 173 реальні прогнози Арестовича, БЕЗ person_id/target_date (плоский
експорт для eval). Цей скрипт — зворотна операція: створює Person + один
RawDocument-заглушку і завантажує прогнози через продакшн-репозиторії (без сирого
SQL). Ідемпотентний: пропускає прогнози, чиї id вже є в БД.

Передумова: docker compose up -d; alembic upgrade head.

Usage:
    .venv/bin/python scripts/retrieval/seed_corpus_from_json.py --corpus scripts/data/retrieval/corpus.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime
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

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from prophet_checker.config import Settings  # noqa: E402
from prophet_checker.models.db import PredictionDB  # noqa: E402
from prophet_checker.models.domain import (  # noqa: E402
    Person,
    Prediction,
    PredictionStatus,
    PredictionStrength,
    PredictionValue,
    RawDocument,
    SourceType,
)
from prophet_checker.storage.postgres import (  # noqa: E402
    PostgresPersonRepository,
    PostgresPredictionRepository,
    PostgresSourceRepository,
)

PERSON_ID = "person:arestovich"
DOCUMENT_ID = "doc:seed:arestovich"
DOCUMENT_URL = "seed://arestovich/corpus"


def _prediction_from_item(item: dict) -> Prediction:
    strength = PredictionStrength(item["strength"]) if item.get("strength") else None
    value = PredictionValue(item["value"]) if item.get("value") else None
    return Prediction(
        id=item["id"],
        document_id=DOCUMENT_ID,
        person_id=PERSON_ID,
        claim_text=item["claim_text"],
        situation=item.get("situation"),
        prediction_date=date.fromisoformat(item["prediction_date"]),
        target_date=None,
        topic=item.get("topic", ""),
        status=PredictionStatus.UNRESOLVED,
        prediction_strength=strength,
        prediction_value=value,
    )


async def _ensure_person(person_repo: PostgresPersonRepository, name: str) -> None:
    existing = await person_repo.get_by_id(PERSON_ID)
    if existing is None:
        await person_repo.save(Person(id=PERSON_ID, name=name))


async def _ensure_document(source_repo: PostgresSourceRepository) -> None:
    existing = await source_repo.get_document_by_url(DOCUMENT_URL)
    if existing is None:
        await source_repo.save_document(
            RawDocument(
                id=DOCUMENT_ID,
                person_id=PERSON_ID,
                source_type=SourceType.TELEGRAM,
                url=DOCUMENT_URL,
                published_at=datetime(2020, 1, 1, tzinfo=UTC),
                raw_text="(seed corpus)",
                language="uk",
            )
        )


async def _existing_prediction_ids(session_factory) -> set[str]:
    async with session_factory() as session:
        result = await session.execute(select(PredictionDB.id))
        return set(result.scalars().all())


async def seed(corpus_path: Path, person_name: str = "Олексій Арестович") -> int:
    """Завантажує corpus.json у БД через продакшн-репозиторії. Повертає к-сть вставлених."""
    settings = Settings()
    count = 0
    async with AsyncExitStack() as stack:
        engine = create_async_engine(settings.database_url, echo=False)
        stack.push_async_callback(engine.dispose)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        person_repo = PostgresPersonRepository(session_factory)
        source_repo = PostgresSourceRepository(session_factory)
        prediction_repo = PostgresPredictionRepository(session_factory)

        await _ensure_person(person_repo, person_name)
        await _ensure_document(source_repo)

        items = json.loads(corpus_path.read_text(encoding="utf-8"))
        already_present = await _existing_prediction_ids(session_factory)

        for item in items:
            if item["id"] in already_present:
                continue
            await prediction_repo.save(_prediction_from_item(item))
            count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=PROJECT_ROOT / "scripts" / "data" / "retrieval" / "corpus.json",
    )
    args = parser.parse_args()
    n = asyncio.run(seed(args.corpus))
    print(f"seeded {n} predictions")


if __name__ == "__main__":
    main()
