# Integration Smoke Script Implementation Plan (Task 19)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/integration_smoke.py` — standalone Python script для manual smoke testing of real services (Postgres, Telegram, Gemini, OpenAI) end-to-end.

**Architecture:** Single file. 5 component-level check functions (`check_postgres`, `check_telegram`, `check_gemini`, `check_openai`, `check_e2e`) dispatched sequentially by `main()`. Required CLI args `--channel` + `--limit`. `--component` для targeted runs. e2e stage monkey-patches `TelegramSource.collect` для early-break after `--limit` yields. Self-seeds `PersonSource` row на first run.

**Tech Stack:** argparse, asyncio, sqlalchemy 2.0 async, telethon, existing prophet_checker components (Settings, factory, ingestion).

**Spec:** [`2026-05-07-integration-smoke-design.md`](2026-05-07-integration-smoke-design.md)

**Test count delta:** 0 (no automated tests — script IS the smoke test). Suite залишається 123.

---

## File Structure (locked-in)

```
scripts/
  integration_smoke.py     NEW — single ~280 line file containing:
                                 - argparse CLI
                                 - 5 check_* async functions
                                 - _ensure_smoke_person_source helper
                                 - _patched_telegram_collect wrapper
                                 - main() runner з progress output
```

Все в одному файлі — pet-project pragmatism. Розбивати по modules при ~500+ рядків.

---

## Task 1: Script skeleton + CLI argparse + main() shell

**Files:**
- Create: `scripts/integration_smoke.py`

### Step 1: Verify script doesn't exist

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
ls scripts/integration_smoke.py 2>&1
```

Expected: `ls: cannot access ...` (file absent).

### Step 2: Create initial `scripts/integration_smoke.py`

```python
from __future__ import annotations

import argparse
import asyncio
import sys


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


async def main() -> int:
    args = parse_args()
    print(f"smoke run: channel={args.channel} limit={args.limit} component={args.component or 'all'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

### Step 3: Verify CLI parsing works

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python scripts/integration_smoke.py --help
```

Expected: prints usage with required `--channel` + `--limit`, optional `--component {postgres,telegram,gemini,openai,e2e}`, `--keep-going`, `--reset-db`.

### Step 4: Verify required args enforced

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python scripts/integration_smoke.py 2>&1
```

Expected: error msg `the following arguments are required: --channel, --limit`. Exit code != 0.

### Step 5: Verify happy parse

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1
```

Expected: prints `smoke run: channel=@arestovich limit=1 component=all`. Exit code 0.

### Step 6: Run full pytest suite — verify no regression

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 123 passing.

### Step 7: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add scripts/integration_smoke.py
git commit -m "feat(smoke): скелет integration smoke script + argparse CLI (Task 19)"
```

---

## Task 2: Stage 1 — `check_postgres`

**Files:**
- Modify: `scripts/integration_smoke.py` (append imports + function + dispatcher entry)

### Step 1: Add imports + module constants at top of file

Find at top of `scripts/integration_smoke.py`:

```python
from __future__ import annotations

import argparse
import asyncio
import sys
```

Replace with:

```python
from __future__ import annotations

import argparse
import asyncio
import sys
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from prophet_checker.config import Settings


EXPECTED_ALEMBIC_HEAD = "edb2e385f26b"
```

### Step 2: Add `check_postgres` function

After `parse_args()` definition, before `main()`, append:

```python
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
```

Returns `(success: bool, message: str)`. Pattern reused for всіх 5 stages.

### Step 3: Wire into `main()` (single-stage demo)

Replace existing `main()`:

```python
async def main() -> int:
    args = parse_args()
    settings = Settings()

    if args.component in (None, "postgres"):
        t0 = time.perf_counter()
        ok, msg = await check_postgres(settings)
        elapsed = time.perf_counter() - t0
        marker = "✓" if ok else "✗"
        print(f"[1/5] postgres ... {marker} ({elapsed:.2f}s)  {msg}")
        if not ok:
            return 1

    return 0
```

### Step 4: Manual smoke — happy path

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
docker compose up -d
sleep 5
.venv/bin/alembic upgrade head
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1 --component postgres
```

Expected output:
```
smoke run: channel=@arestovich limit=1 component=postgres
[1/5] postgres ... ✓ (0.XXs)  alembic head + pgvector ext OK
```

Exit code 0.

### Step 5: Manual smoke — fail path (postgres down)

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
docker compose down
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1 --component postgres
```

Expected: connection error trace OR `[1/5] postgres ... ✗ ...` з message що postgres unreachable. Exit code 1.

Bring back up для подальших stages:
```bash
docker compose up -d
sleep 5
```

### Step 6: Run pytest — no regression

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 123 passing.

### Step 7: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add scripts/integration_smoke.py
git commit -m "feat(smoke): stage 1 — check_postgres (alembic + pgvector) (Task 19)"
```

---

## Task 3: Stage 2 — `check_telegram`

**Files:**
- Modify: `scripts/integration_smoke.py` (add imports + function + main wiring)

### Step 1: Add Telethon import at top of file

Find existing imports:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from prophet_checker.config import Settings
```

Replace with:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from telethon import TelegramClient

from prophet_checker.config import Settings
```

### Step 2: Add `check_telegram` function

After `check_postgres`, append:

```python
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
```

### Step 3: Wire into `main()` — extend dispatcher

Replace existing `main()`:

```python
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

    if failures:
        print(f"\nFAIL: {len(failures)} stage(s) failed ({', '.join(failures)})")
        return 1
    print("\nPASS")
    return 0
```

### Step 4: Manual smoke — happy path (requires real Telegram session)

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1 --component telegram
```

Expected output:
```
smoke run: channel=@arestovich limit=1 component=telegram
[2/5] telegram ... ✓ (X.XXs)  3 messages fetched

PASS
```

Якщо `tg_session.session` не auth'нутий — output буде interactive OTP prompt (first time) або session error. Document workaround у README окремо якщо session відсутня.

### Step 5: Run pytest — no regression

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 123 passing.

### Step 6: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add scripts/integration_smoke.py
git commit -m "feat(smoke): stage 2 — check_telegram (Telethon connect + iter) (Task 19)"
```

---

## Task 4: Stage 3 — `check_gemini`

**Files:**
- Modify: `scripts/integration_smoke.py`

### Step 1: Add LLM imports at top

Find:

```python
from telethon import TelegramClient

from prophet_checker.config import Settings
```

Replace with:

```python
from telethon import TelegramClient

from prophet_checker.config import Settings
from prophet_checker.llm import EmbeddingClient, LLMClient
from prophet_checker.llm.prompts import (
    EXTRACTION_SYSTEM,
    build_extraction_prompt,
    parse_extraction_response,
)
```

### Step 2: Add module constants for sample texts

After `EXPECTED_ALEMBIC_HEAD = "edb2e385f26b"`, append:

```python
SAMPLE_TEXT = (
    "15 жовтня закінчиться війна, до Києва прибуде делегація НАТО "
    "з гарантіями безпеки до кінця року."
)
SAMPLE_CLAIM = "Контрнаступ почнеться влітку 2024 року"
```

### Step 3: Add `check_gemini` function

After `check_telegram`, append:

```python
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
```

### Step 4: Wire into `main()` — add stage 3 block

In `main()`, AFTER the telegram block and BEFORE the failures summary, insert:

```python
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
```

### Step 5: Manual smoke — happy path (requires real GEMINI key)

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1 --component gemini
```

Expected:
```
smoke run: channel=@arestovich limit=1 component=gemini
[3/5] gemini ... ✓ (X.XXs)  response parsed to list[1 predictions]

PASS
```

(Список predictions може бути 0 або більше — обидва valid.)

### Step 6: Run pytest

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 123 passing.

### Step 7: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add scripts/integration_smoke.py
git commit -m "feat(smoke): stage 3 — check_gemini (LLMClient extract sample) (Task 19)"
```

---

## Task 5: Stage 4 — `check_openai`

**Files:**
- Modify: `scripts/integration_smoke.py`

### Step 1: Add `check_openai` function

After `check_gemini`, append:

```python
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
```

### Step 2: Wire into `main()` — add stage 4 block

After the gemini block, BEFORE the failures summary, insert:

```python
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
```

### Step 3: Manual smoke

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1 --component openai
```

Expected:
```
smoke run: channel=@arestovich limit=1 component=openai
[4/5] openai ... ✓ (X.XXs)  1536-dim vector returned

PASS
```

### Step 4: Run pytest

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 123 passing.

### Step 5: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add scripts/integration_smoke.py
git commit -m "feat(smoke): stage 4 — check_openai (EmbeddingClient 1536-dim) (Task 19)"
```

---

## Task 6: Stage 5 — `check_e2e` (self-seed + monkey-patch + run_cycle)

**Files:**
- Modify: `scripts/integration_smoke.py`

This is the largest task — multiple sub-steps. Self-seed PersonSource, monkey-patch TelegramSource.collect для early-break, run orchestrator, assert success.

### Step 1: Add datetime + sqlalchemy + factory imports at top

Find existing imports:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
```

Replace with:

```python
from contextlib import AsyncExitStack
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
```

And after existing `from prophet_checker.llm.prompts import ...` block, append:

```python
from prophet_checker.factory import build_orchestrator
from prophet_checker.models.db import PersonDB, PersonSourceDB
```

### Step 2: Add module constants for smoke seed

After existing constants (`EXPECTED_ALEMBIC_HEAD`, `SAMPLE_TEXT`, `SAMPLE_CLAIM`), append:

```python
SMOKE_PS_ID = "smoke-test"
SMOKE_PERSON_ID = "smoke-test-person"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
```

### Step 3: Add `_ensure_smoke_person_source` helper

After the existing check functions, before `main()`, append:

```python
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
```

### Step 4: Add `_patch_telegram_with_limit` helper

After `_ensure_smoke_person_source`, append:

```python
def _patch_telegram_with_limit(orchestrator, limit: int) -> None:
    from prophet_checker.models.domain import SourceType

    tg_source = orchestrator._sources[SourceType.TELEGRAM]
    original_collect = tg_source.collect

    async def limited_collect(person_source, since=None):
        count = 0
        async for doc in original_collect(person_source, since=since):
            if count >= limit:
                break
            yield doc
            count += 1

    tg_source.collect = limited_collect
```

### Step 5: Add `check_e2e` function

After `_patch_telegram_with_limit`, append:

```python
async def check_e2e(
    settings: Settings, channel: str, limit: int, reset: bool
) -> tuple[bool, str]:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        await _ensure_smoke_person_source(session_factory, channel, reset)

        async with AsyncExitStack() as stack:
            orchestrator = await build_orchestrator(settings, stack)
            _patch_telegram_with_limit(orchestrator, limit)
            report = await orchestrator.run_cycle()

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
```

### Step 6: Wire e2e into `main()` — add stage 5 block

After the openai block, BEFORE the failures summary, insert:

```python
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
```

### Step 7: Manual smoke — `--component e2e`

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1 --component e2e
```

Expected (first run):
```
smoke run: channel=@arestovich limit=1 component=e2e
[5/5] e2e (limit=1) ... ✓ (X.XXs)  posts=1 with_predictions=0-1 saved=N cursor→2024-XX-XX

PASS
```

### Step 8: Manual smoke — full sequential run

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1
```

Expected: all 5 stages run sequentially, each `[N/5] ... ✓`. Final `PASS`.

### Step 9: Manual smoke — `--reset-db` cleanup

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1 --component e2e --reset-db
```

Expected: row dropped first, then re-seeded, e2e processes 1 post (cursor=epoch → newest message).

### Step 10: Run pytest

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 123 passing.

### Step 11: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add scripts/integration_smoke.py
git commit -m "feat(smoke): stage 5 — check_e2e (self-seed + run_cycle з --limit) (Task 19)"
```

---

## Final verification

### Step 1: Verify all 5 stages pass у full run

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1
```

Expected output (example):
```
smoke run: channel=@arestovich limit=1 component=all
[1/5] postgres ... ✓ (0.12s)  alembic head + pgvector ext OK
[2/5] telegram ... ✓ (1.43s)  3 messages fetched
[3/5] gemini ... ✓ (2.81s)  response parsed to list[1 predictions]
[4/5] openai ... ✓ (0.31s)  1536-dim vector returned
[5/5] e2e (limit=1) ... ✓ (4.12s)  posts=1 with_predictions=1 saved=2 cursor→2026-XX-XX

PASS
```

### Step 2: Verify `--keep-going` semantics

Temporarily break Gemini key (e.g., `unset GEMINI_API_KEY` або set до bad value):

```bash
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1 --keep-going
```

Expected: stage 3 fails з `✗`, stages 4 + 5 continue. Final summary `FAIL: N stage(s) failed (gemini, ...)`. Exit 1.

### Step 3: Verify `--component` isolation

```bash
.venv/bin/python scripts/integration_smoke.py --channel @arestovich --limit 1 --component postgres
```

Expected: only stage 1 runs. No API costs. Exit 0.

### Step 4: Verify pytest unchanged

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: **123 passing** (no test count delta from Task 19).

### Step 5: Cleanup

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
docker compose down
```

Smoke writes persist у `pgdata` volume. Для повного reset: `docker compose down -v`.

---

## Out of Scope (deferred)

- ❌ **Automated tests for smoke script itself** — script IS the test
- ❌ **CI integration** — manual smoke; GitHub Actions при AWS deploy
- ❌ **Recorded fixtures (vcr.py)** — кожен run hits real services
- ❌ **Failure injection** — orchestrator unit tests already cover edge cases
- ❌ **Multi-channel single-invocation** — `--channel` accepts ОДИН; multiple runs для multiple channels
- ❌ **Telegram interactive auth bootstrap** — assume tg_session exists; document workflow окремо
- ❌ **Performance regression detection** — informal latency через progress timestamps

---

## Cross-references

- **Spec:** [`2026-05-07-integration-smoke-design.md`](2026-05-07-integration-smoke-design.md)
- **Task 17 Docker Compose:** [`2026-05-07-docker-compose-design.md`](2026-05-07-docker-compose-design.md)
- **Task 16 FastAPI:** [`2026-05-05-fastapi-http-trigger-design.md`](2026-05-05-fastapi-http-trigger-design.md)
- **Task 15 IngestionOrchestrator:** [`2026-05-01-ingestion-orchestrator-design.md`](2026-05-01-ingestion-orchestrator-design.md)
- **TelegramSource ordering fix (`0238b61`)** — prerequisite landed during Task 19 brainstorm
