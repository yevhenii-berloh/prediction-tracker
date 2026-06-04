# Ingestion-run wrapper + FK persistence fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CLI wrapper that runs the real ingestion cycle (Telegram → extract → persist) to populate Postgres with predictions, plus the FK fix that makes persistence actually work.

**Architecture:** Fix `IngestionOrchestrator` to persist the `raw_document` before its predictions (satisfying the FK); make `limit` a real `collect` parameter (removing a monkey-patch); add a thin `run_ingestion.py` wrapper around `build_orchestrator`/`run_cycle`.

**Tech Stack:** Python 3.12, async, SQLAlchemy async, Telethon, pytest (`asyncio_mode=auto`).

**Spec:** `docs/ingestion-to-aws/2026-06-03-ingestion-run-wrapper-design.md` (`90be343`).

**Working dir:** prefix every command with `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker`.

**Test baseline:** 198 passed. Final expected: 202 (+1 T1, +1 T2, +2 T3).

**Plan refinements (vs spec):** (a) `integration_smoke._ensure_smoke_person_source` is left as-is — only its monkey-patch is removed (T4); the shared `ensure_person_source` (T5) serves the wrapper. (b) `IngestionOrchestrator` constructor unchanged.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/prophet_checker/storage/postgres.py` | persistence | `save_document(session=)` + merge |
| `src/prophet_checker/storage/interfaces.py` | protocols | `save_document(session=)`, `collect(limit=)` |
| `src/prophet_checker/sources/base.py` | Source protocol | `collect(..., limit=None)` |
| `src/prophet_checker/sources/mock.py` | test source | `collect(..., limit=None)` honors it |
| `src/prophet_checker/sources/telegram.py` | Telethon source | `collect(..., limit=None)` break after N |
| `src/prophet_checker/ingestion/orchestrator.py` | coordinator | persist raw_doc; `run_cycle(limit=None)` |
| `tests/fakes.py` | fakes | `FakeSourceRepo.save_document(session=None)` |
| `scripts/integration_smoke.py` | smoke | drop monkey-patch, use `run_cycle(limit=)` |
| `scripts/_ingestion_setup.py` | shared seed | **new** `ensure_person_source` |
| `scripts/run_ingestion.py` | wrapper CLI | **new** |
| `tests/test_storage_postgres.py` | tests | save_document session test |
| `tests/test_ingestion_orchestrator.py` | tests | persist docs + limit |
| `docs/verification-track/20-verification-orchestrator/real-db-smoke.md` | runbook | Step 1 → run_ingestion.py |

**Models:** T1 HAIKU, T2 SONNET, T3 SONNET, T4 HAIKU, T5 HAIKU, T6 HAIKU, T7 HAIKU.

---

### Task 1: `save_document` gains a `session=` param (merge-idempotent)

**Files:**
- Modify: `src/prophet_checker/storage/postgres.py` (`save_document`, ~lines 152-157)
- Modify: `src/prophet_checker/storage/interfaces.py` (line 30)
- Modify: `tests/fakes.py` (`FakeSourceRepo.save_document`, ~line 57)
- Test: `tests/test_storage_postgres.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_storage_postgres.py`:

```python
async def test_save_document_with_session_merges_without_commit():
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock

    from prophet_checker.models.domain import RawDocument, SourceType
    from prophet_checker.storage.postgres import PostgresSourceRepository

    session = MagicMock()
    session.merge = AsyncMock()
    session.commit = AsyncMock()
    factory = MagicMock()

    repo = PostgresSourceRepository(factory)
    doc = RawDocument(
        id="d1", person_id="p1", source_type=SourceType.TELEGRAM,
        url="u", published_at=datetime(2022, 1, 1, tzinfo=UTC), raw_text="t",
    )

    await repo.save_document(doc, session=session)

    session.merge.assert_awaited_once()
    session.commit.assert_not_called()
    factory.assert_not_called()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_storage_postgres.py::test_save_document_with_session_merges_without_commit -v`
Expected: FAIL — `save_document()` got an unexpected keyword `session` (current signature has no `session`).

- [ ] **Step 3: Implement**

In `src/prophet_checker/storage/postgres.py`, replace `save_document`:

```python
    async def save_document(
        self, doc: RawDocument, session: AsyncSession | None = None
    ) -> RawDocument:
        db_obj = domain_to_raw_document_db(doc)
        if session is not None:
            await session.merge(db_obj)
            return doc
        async with self._session_factory() as own_session:
            own_session.add(db_obj)
            await own_session.commit()
            return doc
```

(`AsyncSession` is already imported in this file.)

In `src/prophet_checker/storage/interfaces.py`, update the Protocol line:

```python
    async def save_document(
        self, doc: RawDocument, session: "AsyncSession | None" = None
    ) -> RawDocument: ...
```

In `tests/fakes.py`, update `FakeSourceRepo.save_document`:

```python
    async def save_document(
        self, doc: RawDocument, session: "AsyncSession | None" = None
    ) -> RawDocument:
        self._documents.append(doc)
        return doc
```

- [ ] **Step 4: Run test + full suite**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_storage_postgres.py::test_save_document_with_session_merges_without_commit -v && .venv/bin/python -m pytest -q`
Expected: new test PASS; full suite **199 passed**.

- [ ] **Step 5: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/storage/postgres.py src/prophet_checker/storage/interfaces.py tests/fakes.py tests/test_storage_postgres.py
git commit -m "$(cat <<'EOF'
feat(storage): save_document приймає session= (merge-ідемпотентно)

Дозволяє зберегти raw_document у спільній транзакції orchestrator'а.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Orchestrator persists `raw_document` (the FK fix)

**Files:**
- Modify: `src/prophet_checker/ingestion/orchestrator.py` (`_process_channel` persist block, ~lines 68-85)
- Test: `tests/test_ingestion_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ingestion_orchestrator.py`:

```python
async def test_run_cycle_persists_raw_documents():
    person_source = PersonSource(
        id="ps1", person_id="p1", source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    docs = [
        RawDocument(
            id=f"tg:arestovich:{i}", person_id="p1", source_type=SourceType.TELEGRAM,
            url=f"https://t.me/arestovich/{i}",
            published_at=datetime(2024, 1, 2 + i, tzinfo=UTC), raw_text=f"Post {i}",
        )
        for i in range(2)
    ]
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(person_source)
    prediction_repo = FakePredictionRepo()
    pred = Prediction(
        id="pred-1", document_id="tg:arestovich:0", person_id="p1",
        claim_text="claim", prediction_date=date(2024, 1, 1),
    )
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=[[pred], []])
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory, source_repo=source_repo,
        prediction_repo=prediction_repo, extractor=extractor,
        embedder=_make_embedder(), sources={SourceType.TELEGRAM: MockSource(docs)},
    )

    await orchestrator.run_cycle()

    saved = {d.id for d in source_repo._documents}
    assert "tg:arestovich:0" in saved
    assert "tg:arestovich:1" in saved
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_ingestion_orchestrator.py::test_run_cycle_persists_raw_documents -v`
Expected: FAIL — `source_repo._documents` is empty (orchestrator never saves documents).

- [ ] **Step 3: Implement**

In `src/prophet_checker/ingestion/orchestrator.py`, in `_process_channel`, the persist blocks become (add `save_document` first in both branches):

```python
                if predictions:
                    report.posts_with_predictions += 1
                    for p in predictions:
                        p.embedding = await self._embedder.embed(p.claim_text)
                    async with self._session_factory() as session:
                        async with session.begin():
                            await self._source_repo.save_document(raw_doc, session=session)
                            for p in predictions:
                                await self._prediction_repo.save(p, session=session)
                            await self._source_repo.update_source_cursor(
                                ps.id, raw_doc.published_at, session=session
                            )
                    report.predictions_extracted += len(predictions)
                else:
                    async with self._session_factory() as session:
                        async with session.begin():
                            await self._source_repo.save_document(raw_doc, session=session)
                            await self._source_repo.update_source_cursor(
                                ps.id, raw_doc.published_at, session=session
                            )
```

- [ ] **Step 4: Run test + full suite**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_ingestion_orchestrator.py -v && .venv/bin/python -m pytest -q`
Expected: new test PASS; existing orchestrator tests still PASS; full suite **200 passed**.

- [ ] **Step 5: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/ingestion/orchestrator.py tests/test_ingestion_orchestrator.py
git commit -m "$(cat <<'EOF'
fix(ingestion): persist raw_document перед predictions (FK fix)

run_cycle зберігав predictions з FK на raw_documents, але сам документ ніколи
не зберігався → FK-падіння проти реальної БД. Тепер save_document у тій самій
транзакції, в обох гілках.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `limit` as a first-class `collect` parameter

**Files:**
- Modify: `src/prophet_checker/sources/base.py` (the `Source` Protocol)
- Modify: `src/prophet_checker/sources/mock.py`, `src/prophet_checker/sources/telegram.py`
- Modify: `src/prophet_checker/ingestion/orchestrator.py` (`run_cycle` + the `collect` call)
- Test: `tests/test_ingestion_orchestrator.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ingestion_orchestrator.py`:

```python
async def test_mock_source_collect_honors_limit():
    person_source = PersonSource(
        id="ps1", person_id="p1", source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    docs = [
        RawDocument(
            id=f"d{i}", person_id="p1", source_type=SourceType.TELEGRAM,
            url=f"u{i}", published_at=datetime(2024, 1, 2 + i, tzinfo=UTC), raw_text="t",
        )
        for i in range(5)
    ]
    collected = [d async for d in MockSource(docs).collect(person_source, limit=2)]
    assert len(collected) == 2


async def test_run_cycle_passes_limit_to_collect():
    person_source = PersonSource(
        id="ps1", person_id="p1", source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    docs = [
        RawDocument(
            id=f"d{i}", person_id="p1", source_type=SourceType.TELEGRAM,
            url=f"u{i}", published_at=datetime(2024, 1, 2 + i, tzinfo=UTC), raw_text="t",
        )
        for i in range(5)
    ]
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(person_source)
    factory, _ = _stub_session_factory()
    orchestrator = IngestionOrchestrator(
        session_factory=factory, source_repo=source_repo,
        prediction_repo=FakePredictionRepo(), extractor=_make_extractor([]),
        embedder=_make_embedder(), sources={SourceType.TELEGRAM: MockSource(docs)},
    )

    report = await orchestrator.run_cycle(limit=1)

    assert report.channels_processed[0].posts_seen == 1
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_ingestion_orchestrator.py -k "limit" -v`
Expected: FAIL — `collect()` / `run_cycle()` got an unexpected keyword `limit`.

- [ ] **Step 3: Implement**

`src/prophet_checker/sources/base.py` — Protocol:

```python
    def collect(
        self,
        person_source: PersonSource,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[RawDocument]:
        ...
```

`src/prophet_checker/sources/mock.py` — `collect`:

```python
    async def collect(
        self,
        person_source: PersonSource,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[RawDocument]:
        cutoff = since or datetime.min.replace(tzinfo=UTC)
        count = 0
        for doc in self._documents:
            if doc.person_id == person_source.person_id and doc.published_at > cutoff:
                if limit is not None and count >= limit:
                    return
                yield doc
                count += 1
```

`src/prophet_checker/sources/telegram.py` — `collect`: add `limit: int | None = None` to the signature, and a counter:

```python
        count = 0
        async for msg in self._client.iter_messages(
            entity, reverse=True, offset_date=since
        ):
            if not msg.text or len(msg.text.strip()) < self._min_text_length:
                continue
            if limit is not None and count >= limit:
                return
            yield RawDocument(
                id=f"tg:{channel}:{msg.id}",
                person_id=person_source.person_id,
                source_type=SourceType.TELEGRAM,
                url=f"https://t.me/{channel}/{msg.id}",
                published_at=msg.date,
                raw_text=msg.text.strip(),
            )
            count += 1
```

`src/prophet_checker/ingestion/orchestrator.py` — `run_cycle` + `_process_channel`: thread `limit`:

```python
    async def run_cycle(self, limit: int | None = None) -> CycleReport:
        started_at = datetime.now(UTC)
        active = await self._source_repo.list_active_sources()
        channels: list[ChannelReport] = []
        for ps in active:
            report = await self._process_channel(ps, limit)
            channels.append(report)
        finished_at = datetime.now(UTC)
        return CycleReport(
            started_at=started_at,
            finished_at=finished_at,
            channels_processed=channels,
        )

    async def _process_channel(self, ps: PersonSource, limit: int | None = None) -> ChannelReport:
```

and the collect call inside `_process_channel`:

```python
            async for raw_doc in source.collect(ps, since=ps.last_collected_at, limit=limit):
```

- [ ] **Step 4: Run tests + full suite**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_ingestion_orchestrator.py -v && .venv/bin/python -m pytest -q`
Expected: 2 new tests PASS; full suite **202 passed**.

- [ ] **Step 5: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/sources/ src/prophet_checker/storage/interfaces.py src/prophet_checker/ingestion/orchestrator.py tests/test_ingestion_orchestrator.py
git commit -m "$(cat <<'EOF'
feat(ingestion): limit як параметр collect/run_cycle

Source.collect(..., limit=None) + run_cycle(limit=None) прокидає його джерелу.
Замінює monkey-patch у integration_smoke.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `integration_smoke` — drop monkey-patch, use `limit`

**Files:**
- Modify: `scripts/integration_smoke.py`

No automated test (smoke script). Verify by import.

- [ ] **Step 1: Remove the monkey-patch + use the param**

In `scripts/integration_smoke.py`: delete the `_patch_telegram_with_limit` function entirely. Find where it's called (in `check_e2e`, ~line 218 `_patch_telegram_with_limit(orchestrator, limit)`) and delete that call. Change the cycle invocation from `report = await orchestrator.run_cycle()` to:

```python
            report = await orchestrator.run_cycle(limit=limit)
```

- [ ] **Step 2: Verify import + --help**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/integration_smoke.py --help`
Expected: argparse help prints, no import/NameError (the removed function isn't referenced).

- [ ] **Step 3: Run full suite (unchanged)**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest -q`
Expected: **202 passed**.

- [ ] **Step 4: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add scripts/integration_smoke.py
git commit -m "$(cat <<'EOF'
refactor(scripts): integration_smoke юзає run_cycle(limit=) замість monkey-patch

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Shared `ensure_person_source` helper

**Files:**
- Create: `scripts/_ingestion_setup.py`

No automated test (operational, needs DB). Verify by import.

- [ ] **Step 1: Create the helper**

Create `scripts/_ingestion_setup.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from prophet_checker.models.db import PersonDB, PersonSourceDB

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


async def ensure_person_source(session_factory: async_sessionmaker, channel: str) -> str:
    person_id = f"person:{channel}"
    ps_id = f"ps:{channel}"
    async with session_factory() as session:
        existing = await session.execute(
            select(PersonSourceDB).where(PersonSourceDB.id == ps_id)
        )
        if existing.scalar_one_or_none() is None:
            session.add(PersonDB(id=person_id, name=channel))
            session.add(
                PersonSourceDB(
                    id=ps_id,
                    person_id=person_id,
                    source_type="telegram",
                    source_identifier=channel,
                    enabled=True,
                    last_collected_at=EPOCH,
                )
            )
            await session.commit()
    return person_id
```

- [ ] **Step 2: Verify import**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); sys.path.insert(0,'src'); from _ingestion_setup import ensure_person_source; print('import OK')"`
Expected: `import OK`.

- [ ] **Step 3: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add scripts/_ingestion_setup.py
git commit -m "$(cat <<'EOF'
feat(scripts): ensure_person_source — спільний ідемпотентний seed person+source

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wrapper CLI `scripts/run_ingestion.py`

**Files:**
- Create: `scripts/run_ingestion.py`

No automated test (needs real Telegram + DB). Verify by `--help`.

- [ ] **Step 1: Create the wrapper**

Create `scripts/run_ingestion.py`:

```python
"""Запуск ingestion-циклу (collect → extract → persist) для каналу. Обгортка навколо
IngestionOrchestrator — наповнює БД прогнозами (для verifier-smoke / production)."""

from __future__ import annotations

import argparse
import asyncio
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
    asyncio.run(main())
```

- [ ] **Step 2: Verify --help**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/run_ingestion.py --help`
Expected: argparse help showing `--channel` and `--limit` (no import error). A real run needs Postgres + Telegram session + `OPENAI_API_KEY` (embeddings) + `LLM_*` (extractor).

- [ ] **Step 3: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add scripts/run_ingestion.py
git commit -m "$(cat <<'EOF'
feat(scripts): run_ingestion CLI — обгортка ingestion для наповнення БД

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Update the real-DB smoke runbook

**Files:**
- Modify: `docs/verification-track/20-verification-orchestrator/real-db-smoke.md`

- [ ] **Step 1: Repoint Step 1 Variant A**

In `real-db-smoke.md`, Step 1 "Варіант A — реальний ingestion", replace the `integration_smoke.py` command block with:

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/run_ingestion.py --channel @arestovich --limit 10
```

And update its description line to: "Прогнати ingestion на N постах (collect → extract → persist)". Keep the "Очікувано: seen=.../extracted=N" expectation (now from the per-channel print line).

- [ ] **Step 2: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add docs/verification-track/20-verification-orchestrator/real-db-smoke.md
git commit -m "$(cat <<'EOF'
docs(verifier): real-db-smoke Step 1 → run_ingestion.py замість integration_smoke

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final Verification

- [ ] **Full suite**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest -q`
Expected: **202 passed**.

- [ ] **Scripts parse**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/run_ingestion.py --help && .venv/bin/python scripts/integration_smoke.py --help`
Expected: both print help, no import errors.

- [ ] **Git state**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git log --oneline -7 && git status --short | grep -v "^??"`
Expected: 7 task commits; working tree clean (tracked).

- [ ] **Scope discipline**

Confirm NOT modified: `alembic/` (no schema change), `models/db.py`, `models/domain.py`, `analysis/`, `llm/`. This task touches storage, sources, ingestion orchestrator, the two scripts, the shared helper, and the two test files + runbook.
