"""Запуск ingestion-циклу (collect → extract → persist) для каналу. Обгортка навколо
IngestionOrchestrator — наповнює БД прогнозами (для verifier-smoke / production)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import AsyncExitStack
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from _ingestion_setup import ensure_person_source
from prophet_checker.config import Settings
from prophet_checker.factory import build_orchestrator


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    settings = Settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await ensure_person_source(session_factory, args.channel)
    await engine.dispose()

    async with AsyncExitStack() as stack:
        orchestrator = await build_orchestrator(settings, stack)
        report = await orchestrator.run_cycle(limit=args.limit)

    for ch in report.channels_processed:
        print(
            f"{ch.person_source_id}: seen={ch.posts_seen} "
            f"with_predictions={ch.posts_with_predictions} "
            f"extracted={ch.predictions_extracted} error={ch.error or '-'}"
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    for _noisy in ("LiteLLM", "litellm", "telethon", "httpx", "httpcore"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    asyncio.run(main())
