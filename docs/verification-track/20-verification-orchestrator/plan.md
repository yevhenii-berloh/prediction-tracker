# VerificationOrchestrator (first-pass) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** First-pass orchestrator that pulls never-verified predictions from the DB, verifies each once through the `Verifier` (Flash Lite), and writes the result + urgency fields back.

**Architecture:** New `verification/` package — a thin `VerificationOrchestrator` coordinates (pull eligible → per-item verify → persist), with pure `apply_verification_result`/`apply_verification_error` functions holding the dict→`Prediction` mapping. `PostgresPredictionRepository.update()` is extended to persist all V2 fields. Per-item try/except (one bad prediction never blocks the rest); retry-eligible failures with an attempt cap.

**Tech Stack:** Python 3.12, async/await, Pydantic v2, SQLAlchemy async, pytest (`asyncio_mode=auto`).

**Spec:** `docs/verification-track/20-verification-orchestrator/design.md` (`d8cd037`).

**Working dir:** prefix every command with `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker` (cwd drifts).

**Test baseline:** 190 passed. Final expected: 198 (+1 T1, +3 T2, +3 T3, +1 T4).

**Deviation from spec:** the spec sketched `VerificationOrchestrator(session_factory, prediction_repo, verifier, attempt_cap)`. `session_factory` is unused — `repo.update()` self-manages its session and first-pass updates are per-item independent. Constructor is `(prediction_repo, verifier, attempt_cap=5)`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/prophet_checker/models/domain.py` | enums | Add `PredictionStatus.PREMATURE` |
| `src/prophet_checker/verification/orchestrator.py` | coordinator + pure mappers | **Create** |
| `src/prophet_checker/verification/report.py` | report models | **Create** |
| `src/prophet_checker/verification/__init__.py` | exports | **Create** |
| `src/prophet_checker/storage/postgres.py` | persistence | Extend `update()` |
| `src/prophet_checker/config.py` | settings | Add `gemini_api_key` |
| `src/prophet_checker/factory.py` | composition root | Add `build_verification_orchestrator` |
| `scripts/run_verification_cycle.py` | CLI trigger | **Create** |
| `tests/test_verification_orchestrator.py` | mapper + orchestrator tests | **Create** |
| `tests/test_models.py` | enum test | Add 1 |
| `tests/test_storage_postgres.py` | update() test | Add 1 |

**Out of scope:** recheck loop, scheduling, DB schema migration (status is `String(20)`, accepts "premature" already).

**Model guidance:** T1 HAIKU, T2 SONNET, T3 SONNET, T4 HAIKU, T5 HAIKU, T6 HAIKU.

---

### Task 1: Add `PREMATURE` to `PredictionStatus`

The verifier produces 4 statuses but the enum has only 3 (confirmed/refuted/unresolved). `apply_verification_result` will do `PredictionStatus("premature")` → must not raise. No migration: `PredictionDB.status` is `String(20)`.

**Files:**
- Modify: `src/prophet_checker/models/domain.py` (the `PredictionStatus` enum, ~lines 14-18)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_prediction_status_has_premature():
    from prophet_checker.models.domain import PredictionStatus

    assert PredictionStatus.PREMATURE == "premature"
    assert PredictionStatus("premature") is PredictionStatus.PREMATURE
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_models.py::test_prediction_status_has_premature -v`
Expected: FAIL — `AttributeError: PREMATURE` / `ValueError: 'premature' is not a valid PredictionStatus`.

- [ ] **Step 3: Add the enum member**

In `src/prophet_checker/models/domain.py`, the `PredictionStatus` enum becomes exactly:

```python
class PredictionStatus(str, Enum):
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    UNRESOLVED = "unresolved"
    PREMATURE = "premature"
```

- [ ] **Step 4: Run test + full suite**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_models.py::test_prediction_status_has_premature -v && .venv/bin/python -m pytest -q`
Expected: new test PASS; full suite **191 passed**.

- [ ] **Step 5: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/models/domain.py tests/test_models.py
git commit -m "$(cat <<'EOF'
feat(models): додаю PredictionStatus.PREMATURE (4-й статус verifier'а)

Без міграції — PredictionDB.status це String(20).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Pure mappers `apply_verification_result` / `apply_verification_error`

Pure functions that map the `verify()` dict onto a `Prediction` copy. No DB, no LLM — fully unit-testable.

**Files:**
- Create: `src/prophet_checker/verification/__init__.py` (empty for now)
- Create: `src/prophet_checker/verification/orchestrator.py`
- Test: `tests/test_verification_orchestrator.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verification_orchestrator.py`:

```python
from __future__ import annotations

from datetime import UTC, date, datetime

from prophet_checker.models.domain import (
    Prediction,
    PredictionStatus,
    PredictionStrength,
    PredictionValue,
)
from prophet_checker.verification.orchestrator import (
    apply_verification_error,
    apply_verification_result,
)

NOW = datetime(2026, 5, 31, tzinfo=UTC)


def _make_prediction(pid="p1", attempts=0):
    return Prediction(
        id=pid,
        document_id="d1",
        person_id="arestovich",
        claim_text="Контрнаступ почнеться влітку",
        situation="Обговорення літньої кампанії",
        prediction_date=date(2022, 1, 15),
        verify_attempts=attempts,
    )


def test_apply_result_confirmed():
    result = {
        "status": "confirmed", "confidence": 0.9, "prediction_strength": "low",
        "prediction_value": "high", "evidence": "Сталось у червні.",
        "retry_after": None, "max_horizon": None,
    }
    out = apply_verification_result(_make_prediction(), result, NOW)
    assert out.status == PredictionStatus.CONFIRMED
    assert out.confidence == 0.9
    assert out.prediction_strength == PredictionStrength.LOW
    assert out.prediction_value == PredictionValue.HIGH
    assert out.evidence_text == "Сталось у червні."
    assert out.verified_at == NOW
    assert out.verify_attempts == 1
    assert out.next_check_at is None
    assert out.max_horizon is None
    assert out.last_verify_error is None
    assert out.last_verify_error_at is None


def test_apply_result_premature_sets_next_check():
    result = {
        "status": "premature", "confidence": 0.6, "prediction_strength": "medium",
        "prediction_value": "high", "evidence": None,
        "retry_after": "2026-09-01", "max_horizon": "2027-01-01",
    }
    out = apply_verification_result(_make_prediction(), result, NOW)
    assert out.status == PredictionStatus.PREMATURE
    assert out.next_check_at == date(2026, 9, 1)
    assert out.max_horizon == date(2027, 1, 1)
    assert out.verified_at == NOW
    assert out.verify_attempts == 1


def test_apply_error_keeps_unverified():
    out = apply_verification_error(_make_prediction(attempts=1), ValueError("bad json"), NOW)
    assert out.verify_attempts == 2
    assert out.last_verify_error == "ValueError: bad json"
    assert out.last_verify_error_at == NOW
    assert out.verified_at is None
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_orchestrator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prophet_checker.verification'`.

- [ ] **Step 3: Create the package + mappers**

Create empty `src/prophet_checker/verification/__init__.py`:

```python
```

Create `src/prophet_checker/verification/orchestrator.py`:

```python
from __future__ import annotations

from datetime import date, datetime

from prophet_checker.models.domain import (
    Prediction,
    PredictionStatus,
    PredictionStrength,
    PredictionValue,
)


def apply_verification_result(prediction: Prediction, result: dict, now: datetime) -> Prediction:
    status = PredictionStatus(result["status"])
    updates = {
        "status": status,
        "confidence": result["confidence"],
        "prediction_strength": PredictionStrength(result["prediction_strength"]),
        "prediction_value": PredictionValue(result["prediction_value"]),
        "evidence_text": result.get("evidence"),
        "verified_at": now,
        "verify_attempts": prediction.verify_attempts + 1,
        "last_verify_error": None,
        "last_verify_error_at": None,
        "next_check_at": None,
        "max_horizon": None,
    }
    if status == PredictionStatus.PREMATURE:
        retry_after = result.get("retry_after")
        if retry_after:
            updates["next_check_at"] = date.fromisoformat(retry_after)
        max_horizon = result.get("max_horizon")
        if max_horizon:
            updates["max_horizon"] = date.fromisoformat(max_horizon)
    return prediction.model_copy(update=updates)


def apply_verification_error(prediction: Prediction, exc: Exception, now: datetime) -> Prediction:
    return prediction.model_copy(update={
        "verify_attempts": prediction.verify_attempts + 1,
        "last_verify_error": f"{type(exc).__name__}: {exc}",
        "last_verify_error_at": now,
    })
```

- [ ] **Step 4: Run tests + full suite**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_orchestrator.py -v && .venv/bin/python -m pytest -q`
Expected: 3 new tests PASS; full suite **194 passed**.

- [ ] **Step 5: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/verification/ tests/test_verification_orchestrator.py
git commit -m "$(cat <<'EOF'
feat(verification): pure mappers apply_verification_result/error

dict→Prediction маппінг + urgency (next_check_at/max_horizon на premature);
error-шлях лишає verified_at=None (retry-eligible).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `VerificationOrchestrator.run_cycle` + report + exports

The coordinator: pull `get_unverified()`, skip attempt-capped, verify each, persist, build a report.

**Files:**
- Create: `src/prophet_checker/verification/report.py`
- Modify: `src/prophet_checker/verification/orchestrator.py` (append class + imports)
- Modify: `src/prophet_checker/verification/__init__.py` (exports)
- Test: `tests/test_verification_orchestrator.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verification_orchestrator.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from fakes import FakePredictionRepo
from prophet_checker.verification.orchestrator import VerificationOrchestrator

CONFIRMED_RESULT = {
    "status": "confirmed", "confidence": 0.9, "prediction_strength": "low",
    "prediction_value": "high", "evidence": "e", "retry_after": None, "max_horizon": None,
}


def _stub_verifier(**kwargs):
    v = MagicMock()
    v.verify = AsyncMock(**kwargs)
    return v


async def test_run_cycle_verifies_eligible():
    repo = FakePredictionRepo()
    await repo.save(_make_prediction("p1"))
    orch = VerificationOrchestrator(repo, _stub_verifier(return_value=CONFIRMED_RESULT))

    report = await orch.run_cycle()

    assert report.verified == 1
    assert report.failed == 0
    assert report.skipped == 0
    saved = (await repo.get_by_person("arestovich"))[0]
    assert saved.status == PredictionStatus.CONFIRMED
    assert saved.verified_at is not None


async def test_run_cycle_skips_attempt_capped():
    repo = FakePredictionRepo()
    await repo.save(_make_prediction("p1", attempts=5))
    verifier = _stub_verifier(return_value=CONFIRMED_RESULT)
    orch = VerificationOrchestrator(repo, verifier, attempt_cap=5)

    report = await orch.run_cycle()

    assert report.skipped == 1
    assert report.verified == 0
    verifier.verify.assert_not_called()


async def test_run_cycle_survives_per_item_failure():
    repo = FakePredictionRepo()
    await repo.save(_make_prediction("p1"))
    await repo.save(_make_prediction("p2"))
    verifier = _stub_verifier(side_effect=[ValueError("boom"), CONFIRMED_RESULT])
    orch = VerificationOrchestrator(repo, verifier)

    report = await orch.run_cycle()

    assert report.failed == 1
    assert report.verified == 1
    preds = {p.id: p for p in await repo.get_by_person("arestovich")}
    assert preds["p1"].verified_at is None
    assert preds["p1"].last_verify_error.startswith("ValueError")
    assert preds["p2"].verified_at is not None
```

- [ ] **Step 2: Run tests, expect FAIL**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_orchestrator.py -k run_cycle -v`
Expected: FAIL — `ImportError: cannot import name 'VerificationOrchestrator'`.

- [ ] **Step 3: Create the report models**

Create `src/prophet_checker/verification/report.py`:

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class VerificationEntry(BaseModel):
    prediction_id: str
    status: str | None = None
    error: str | None = None


class VerificationCycleReport(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    verified: int = 0
    failed: int = 0
    skipped: int = 0
    entries: list[VerificationEntry] = Field(default_factory=list)
```

- [ ] **Step 4: Append the orchestrator class**

At the top of `src/prophet_checker/verification/orchestrator.py`, add to the imports:

```python
from datetime import UTC, date, datetime
```

(replace the existing `from datetime import date, datetime` line) and add:

```python
from prophet_checker.verification.report import VerificationCycleReport, VerificationEntry
```

Then append the class at the end of the file:

```python
class VerificationOrchestrator:
    def __init__(self, prediction_repo, verifier, attempt_cap: int = 5) -> None:
        self._prediction_repo = prediction_repo
        self._verifier = verifier
        self._attempt_cap = attempt_cap

    async def run_cycle(self, limit: int | None = None, today: date | None = None) -> VerificationCycleReport:
        started = datetime.now(UTC)
        today_str = (today or started.date()).isoformat()
        candidates = await self._prediction_repo.get_unverified()
        eligible = [p for p in candidates if p.verify_attempts < self._attempt_cap]
        skipped = len(candidates) - len(eligible)
        if limit is not None:
            eligible = eligible[:limit]
        report = VerificationCycleReport(started_at=started, skipped=skipped)
        for p in eligible:
            try:
                result = await self._verifier.verify(
                    claim=p.claim_text,
                    situation=p.situation,
                    prediction_date=p.prediction_date.isoformat(),
                    target_date=p.target_date.isoformat() if p.target_date else None,
                    today=today_str,
                )
                updated = apply_verification_result(p, result, started)
                report.verified += 1
                report.entries.append(VerificationEntry(prediction_id=p.id, status=updated.status.value))
            except Exception as exc:
                updated = apply_verification_error(p, exc, started)
                report.failed += 1
                report.entries.append(
                    VerificationEntry(prediction_id=p.id, error=f"{type(exc).__name__}: {exc}")
                )
            await self._prediction_repo.update(updated)
        report.finished_at = datetime.now(UTC)
        return report
```

- [ ] **Step 5: Fill the package exports**

Replace `src/prophet_checker/verification/__init__.py` with:

```python
from prophet_checker.verification.orchestrator import VerificationOrchestrator
from prophet_checker.verification.report import VerificationCycleReport, VerificationEntry

__all__ = ["VerificationOrchestrator", "VerificationCycleReport", "VerificationEntry"]
```

- [ ] **Step 6: Run tests + full suite**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_orchestrator.py -v && .venv/bin/python -m pytest -q`
Expected: all `test_verification_orchestrator` tests PASS; full suite **197 passed**.

- [ ] **Step 7: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/verification/ tests/test_verification_orchestrator.py
git commit -m "$(cat <<'EOF'
feat(verification): VerificationOrchestrator.run_cycle + report

Pull get_unverified → skip attempt-capped → verify (Verifier) → persist.
Per-item try/except (retry-eligible); VerificationCycleReport counts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Extend `PostgresPredictionRepository.update()`

`update()` currently writes only 5 fields. Persist all V2 fields (the `domain_to_prediction_db` mapper already maps them; only `update()` lags). Tested via a mocked session (postgres repo isn't DB-tested in this project).

**Files:**
- Modify: `src/prophet_checker/storage/postgres.py` (`update()`, ~lines 249-259)
- Test: `tests/test_storage_postgres.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_storage_postgres.py`:

```python
async def test_update_persists_v2_fields():
    from datetime import UTC, date, datetime
    from unittest.mock import AsyncMock, MagicMock

    from prophet_checker.models.domain import (
        Prediction, PredictionStatus, PredictionStrength, PredictionValue,
    )
    from prophet_checker.storage.postgres import PostgresPredictionRepository

    db_obj = MagicMock()
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.get = AsyncMock(return_value=db_obj)
    session.commit = AsyncMock()
    factory = MagicMock(return_value=session)

    repo = PostgresPredictionRepository(factory)
    pred = Prediction(
        id="p1", document_id="d1", person_id="arestovich", claim_text="c",
        prediction_date=date(2022, 1, 1), status=PredictionStatus.PREMATURE, confidence=0.6,
        prediction_strength=PredictionStrength.MEDIUM, prediction_value=PredictionValue.HIGH,
        next_check_at=date(2026, 9, 1), max_horizon=date(2027, 1, 1), verify_attempts=2,
        verified_at=datetime(2026, 5, 31, tzinfo=UTC),
    )

    await repo.update(pred)

    assert db_obj.status == "premature"
    assert db_obj.prediction_strength == "medium"
    assert db_obj.prediction_value == "high"
    assert db_obj.next_check_at == date(2026, 9, 1)
    assert db_obj.max_horizon == date(2027, 1, 1)
    assert db_obj.verify_attempts == 2
    assert db_obj.verified_at == datetime(2026, 5, 31, tzinfo=UTC)
    session.commit.assert_awaited()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_storage_postgres.py::test_update_persists_v2_fields -v`
Expected: FAIL — `db_obj.prediction_strength` is a fresh `MagicMock`, not `"medium"` (current `update()` never sets it).

- [ ] **Step 3: Extend `update()`**

Replace the body of `PostgresPredictionRepository.update()` in `src/prophet_checker/storage/postgres.py` with:

```python
    async def update(self, prediction: Prediction) -> Prediction:
        async with self._session_factory() as session:
            db_obj = await session.get(PredictionDB, prediction.id)
            if db_obj:
                db_obj.status = prediction.status.value
                db_obj.confidence = prediction.confidence
                db_obj.evidence_url = prediction.evidence_url
                db_obj.evidence_text = prediction.evidence_text
                db_obj.prediction_strength = (
                    prediction.prediction_strength.value if prediction.prediction_strength else None
                )
                db_obj.prediction_value = (
                    prediction.prediction_value.value if prediction.prediction_value else None
                )
                db_obj.max_horizon = prediction.max_horizon
                db_obj.next_check_at = prediction.next_check_at
                db_obj.verify_attempts = prediction.verify_attempts
                db_obj.last_verify_error = prediction.last_verify_error
                db_obj.last_verify_error_at = prediction.last_verify_error_at
                db_obj.verified_at = prediction.verified_at
                await session.commit()
            return prediction
```

- [ ] **Step 4: Run test + full suite**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_storage_postgres.py::test_update_persists_v2_fields -v && .venv/bin/python -m pytest -q`
Expected: new test PASS; full suite **198 passed**.

- [ ] **Step 5: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/storage/postgres.py tests/test_storage_postgres.py
git commit -m "$(cat <<'EOF'
feat(storage): update() персистить усі V2-поля verification

strength/value/max_horizon/next_check_at/verify_attempts/last_verify_error(_at).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Factory wiring + config

Add a `gemini_api_key` setting and a `build_verification_orchestrator` composition root (no Telegram; Flash Lite `Verifier`).

**Files:**
- Modify: `src/prophet_checker/config.py`
- Modify: `src/prophet_checker/factory.py`

No automated test (a composition root needs a real engine; consistent with `build_orchestrator` having none). Verified by import + Task 6 manual smoke.

- [ ] **Step 1: Add the config field**

In `src/prophet_checker/config.py`, add this field next to the other LLM settings (after `llm_api_key`):

```python
    gemini_api_key: str = ""
```

- [ ] **Step 2: Add the factory function**

In `src/prophet_checker/factory.py`, add these imports near the existing ones:

```python
from prophet_checker.analysis.verifier import Verifier
from prophet_checker.verification import VerificationOrchestrator
```

Then add this function after `build_orchestrator`:

```python
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
```

- [ ] **Step 3: Verify import + full suite**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "from prophet_checker.factory import build_verification_orchestrator; from prophet_checker.config import Settings; Settings()" && .venv/bin/python -m pytest -q`
Expected: import OK (no error); full suite **198 passed** (unchanged).

- [ ] **Step 4: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/config.py src/prophet_checker/factory.py
git commit -m "$(cat <<'EOF'
feat(factory): build_verification_orchestrator + gemini_api_key setting

Composition root для VerificationOrchestrator (Flash Lite Verifier, без Telegram).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: CLI entrypoint

Thin manual trigger: build via factory, run one cycle, print the report.

**Files:**
- Create: `scripts/run_verification_cycle.py`

No automated test (operational; needs real DB + LLM). Optional manual smoke noted below.

- [ ] **Step 1: Create the script**

Create `scripts/run_verification_cycle.py`:

```python
"""Запуск одного циклу верифікації (first-pass): бере unverified прогнози з БД,
проганяє через Verifier (Flash Lite), пише результати назад, друкує report."""

from __future__ import annotations

import argparse
import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from prophet_checker.config import Settings
from prophet_checker.factory import build_verification_orchestrator


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    settings = Settings()
    async with AsyncExitStack() as stack:
        orchestrator = await build_verification_orchestrator(settings, stack)
        report = await orchestrator.run_cycle(limit=args.limit)

    print(f"verified={report.verified} failed={report.failed} skipped={report.skipped}")
    for e in report.entries:
        print(f"  {e.prediction_id}: {e.status or ('ERROR ' + (e.error or ''))}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Verify it parses + --help works**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/run_verification_cycle.py --help`
Expected: argparse help prints (no import/syntax error). A real run needs Postgres up + `GEMINI_API_KEY` in `.env`; if unavailable, that's fine — the script is the deliverable.

- [ ] **Step 3: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add scripts/run_verification_cycle.py
git commit -m "$(cat <<'EOF'
feat(scripts): run_verification_cycle CLI — ручний запуск verification cycle

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final Verification

- [ ] **Full suite**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest -q`
Expected: **198 passed**.

- [ ] **Git state**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git log --oneline -6 && git status --short | grep -v "^??"`
Expected: 6 task commits (T1–T6); working tree clean (only untracked `.DS_Store`/`.coverage`).

- [ ] **Scope discipline**

Confirm NOT modified: `alembic/` (no migration — `status` is varchar), `models/db.py`, `llm/prompts.py`, `analysis/verifier.py`, `scripts/verification_eval.py`. This task touches only `models/domain.py`, the new `verification/` package, `storage/postgres.py`, `config.py`, `factory.py`, `scripts/run_verification_cycle.py`, and the three test files.
