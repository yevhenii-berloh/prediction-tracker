from __future__ import annotations

import argparse
import asyncio
import sys
import time

from contextlib import AsyncExitStack
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from telethon import TelegramClient

from prophet_checker.config import Settings
from prophet_checker.llm import EmbeddingClient, LLMClient
from prophet_checker.llm.prompts import (
    EXTRACTION_SYSTEM,
    build_extraction_prompt,
    parse_extraction_response,
)
from prophet_checker.factory import build_orchestrator
from prophet_checker.models.db import PersonDB, PersonSourceDB


EXPECTED_ALEMBIC_HEAD = "cef3b9130690"

SAMPLE_TEXT = (
    "15 жовтня закінчиться війна, до Києва прибуде делегація НАТО "
    "з гарантіями безпеки до кінця року."
)
SAMPLE_CLAIM = "Контрнаступ почнеться влітку 2024 року"

SMOKE_PS_ID = "smoke-test"
SMOKE_PERSON_ID = "smoke-test-person"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


CHECKS = ["postgres", "telegram", "gemini", "openai", "e2e"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="integration_smoke",
        description="Manual smoke test for real Postgres + Telegram + Gemini + OpenAI integration.",
    )
    parser.add_argument(
        "--channel",
        required=True,
        help="Telegram channel username (with or without @)",
    )
    parser.add_argument(
        "--limit",
        required=True,
        type=int,
        help="Max posts to process during e2e cycle. Cost: ~$0.001 × N.",
    )
    parser.add_argument(
        "--component",
        choices=CHECKS,
        default=None,
        help="Run only one stage. Default: run all 5 sequentially.",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Don't halt on first fail; run all stages, accumulate errors.",
    )
    parser.add_argument(
        "--reset-db",
        action="store_true",
        help="Drop smoke PersonSource + cascading rows before run.",
    )
    return parser.parse_args()


async def check_postgres(settings: Settings) -> tuple[bool, str]:
    engine = create_async_engine(settings.database_url, echo=False)
    try:
        async with engine.connect() as conn:
            alembic = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = alembic.scalar_one_or_none()
            if row != EXPECTED_ALEMBIC_HEAD:
                return False, f"alembic_version is {row!r}, expected {EXPECTED_ALEMBIC_HEAD!r}"

            ext = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname='vector'")
            )
            if ext.scalar_one_or_none() != "vector":
                return False, "pgvector extension not installed"

            return True, "alembic head + pgvector ext OK"
    finally:
        await engine.dispose()


async def check_telegram(settings: Settings, channel: str) -> tuple[bool, str]:
    client = TelegramClient(
        session=settings.tg_session_path,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    try:
        await client.start()
        entity = await client.get_entity(channel)
        messages = []
        async for msg in client.iter_messages(entity, limit=3):
            messages.append(msg)
        if not messages:
            return False, f"channel {channel!r} returned 0 messages"
        return True, f"{len(messages)} messages fetched"
    finally:
        await client.disconnect()


async def check_gemini(settings: Settings) -> tuple[bool, str]:
    llm = LLMClient(
        provider=settings.llm_provider,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
    )
    prompt = build_extraction_prompt(
        text=SAMPLE_TEXT,
        person_name="smoke-test-author",
        published_date="2024-09-01",
    )
    response = await llm.complete(prompt, system=EXTRACTION_SYSTEM)
    parsed = parse_extraction_response(response)
    if not isinstance(parsed, list):
        return False, f"parsed response is {type(parsed).__name__}, expected list"
    return True, f"response parsed to list[{len(parsed)} predictions]"


async def check_openai(settings: Settings) -> tuple[bool, str]:
    embedder = EmbeddingClient(
        model=settings.embedding_model,
        api_key=settings.openai_api_key,
    )
    vector = await embedder.embed(SAMPLE_CLAIM)
    if not isinstance(vector, list):
        return False, f"got {type(vector).__name__}, expected list"
    if len(vector) != 1536:
        return False, f"vector dim {len(vector)}, expected 1536"
    return True, f"1536-dim vector returned"


async def _ensure_smoke_person_source(
    session_factory: async_sessionmaker, channel: str, reset: bool
) -> None:
    async with session_factory() as session:
        if reset:
            await session.execute(
                text("DELETE FROM predictions WHERE person_id = :pid"),
                {"pid": SMOKE_PERSON_ID},
            )
            await session.execute(
                text("DELETE FROM raw_documents WHERE person_id = :pid"),
                {"pid": SMOKE_PERSON_ID},
            )
            await session.execute(
                text("DELETE FROM person_sources WHERE id = :id"),
                {"id": SMOKE_PS_ID},
            )
            await session.execute(
                text("DELETE FROM persons WHERE id = :pid"),
                {"pid": SMOKE_PERSON_ID},
            )
            await session.commit()

        existing = await session.execute(
            select(PersonSourceDB).where(PersonSourceDB.id == SMOKE_PS_ID)
        )
        if existing.scalar_one_or_none() is not None:
            return

        session.add(PersonDB(id=SMOKE_PERSON_ID, name="Smoke Test"))
        session.add(
            PersonSourceDB(
                id=SMOKE_PS_ID,
                person_id=SMOKE_PERSON_ID,
                source_type="telegram",
                source_identifier=channel,
                enabled=True,
                last_collected_at=EPOCH,
            )
        )
        await session.commit()


async def check_e2e(
    settings: Settings, channel: str, limit: int, reset: bool
) -> tuple[bool, str]:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        await _ensure_smoke_person_source(session_factory, channel, reset)

        async with AsyncExitStack() as stack:
            orchestrator = await build_orchestrator(settings, stack)
            report = await orchestrator.run_cycle(limit=limit)

        if not report.channels_processed:
            return False, "report.channels_processed is empty"

        smoke_channel = next(
            (ch for ch in report.channels_processed if ch.person_source_id == SMOKE_PS_ID),
            None,
        )
        if smoke_channel is None:
            return False, f"no channel report for {SMOKE_PS_ID}"
        if smoke_channel.error is not None:
            return False, f"halted: {smoke_channel.error}"

        return True, (
            f"posts={smoke_channel.posts_seen} "
            f"with_predictions={smoke_channel.posts_with_predictions} "
            f"saved={smoke_channel.predictions_extracted} "
            f"cursor→{smoke_channel.cursor_advanced_to}"
        )
    finally:
        await engine.dispose()


async def main() -> int:
    args = parse_args()
    settings = Settings()
    failures: list[str] = []

    if args.component in (None, "postgres"):
        t0 = time.perf_counter()
        ok, msg = await check_postgres(settings)
        elapsed = time.perf_counter() - t0
        marker = "✓" if ok else "✗"
        print(f"[1/5] postgres ... {marker} ({elapsed:.2f}s)  {msg}")
        if not ok:
            failures.append("postgres")
            if not args.keep_going:
                return 1

    if args.component in (None, "telegram"):
        t0 = time.perf_counter()
        ok, msg = await check_telegram(settings, args.channel)
        elapsed = time.perf_counter() - t0
        marker = "✓" if ok else "✗"
        print(f"[2/5] telegram ... {marker} ({elapsed:.2f}s)  {msg}")
        if not ok:
            failures.append("telegram")
            if not args.keep_going:
                return 1

    if args.component in (None, "gemini"):
        t0 = time.perf_counter()
        ok, msg = await check_gemini(settings)
        elapsed = time.perf_counter() - t0
        marker = "✓" if ok else "✗"
        print(f"[3/5] gemini ... {marker} ({elapsed:.2f}s)  {msg}")
        if not ok:
            failures.append("gemini")
            if not args.keep_going:
                return 1

    if args.component in (None, "openai"):
        t0 = time.perf_counter()
        ok, msg = await check_openai(settings)
        elapsed = time.perf_counter() - t0
        marker = "✓" if ok else "✗"
        print(f"[4/5] openai ... {marker} ({elapsed:.2f}s)  {msg}")
        if not ok:
            failures.append("openai")
            if not args.keep_going:
                return 1

    if args.component in (None, "e2e"):
        t0 = time.perf_counter()
        ok, msg = await check_e2e(settings, args.channel, args.limit, args.reset_db)
        elapsed = time.perf_counter() - t0
        marker = "✓" if ok else "✗"
        print(f"[5/5] e2e (limit={args.limit}) ... {marker} ({elapsed:.2f}s)  {msg}")
        if not ok:
            failures.append("e2e")
            if not args.keep_going:
                return 1

    if failures:
        print(f"\nFAIL: {len(failures)} stage(s) failed ({', '.join(failures)})")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
