# Task 19.5 — Schema + V2 Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land schema migration + V2 prompts/parser foundations для Verifier v2. Clean delete V1 (idle, no callers). Foundation only — no orchestrator (Task 20), no model eval (Task 19.7).

**Architecture:** Six-task TDD chain — V1 delete first (clean slate), then domain/DB/mappers in parallel-safe order, then V2 prompts/parser, then Alembic migration. Each task self-contained, committable independently.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2.0 async, Alembic, pgvector, pytest-asyncio.

**Spec:** [`2026-05-07-task-19-5-schema-prompts-design.md`](2026-05-07-task-19-5-schema-prompts-design.md)

**Test count delta:** +17 new − 7 V1 deletes = **+10 net**. 123 → **133**.

---

## File Structure (locked-in)

```
src/prophet_checker/
  models/
    domain.py                MODIFIED: add PredictionStrength enum + 6 Prediction fields
    db.py                    MODIFIED: add 6 PredictionDB columns + Index
  storage/
    postgres.py              MODIFIED: mapper round-trip нових fields
  llm/
    prompts.py               MODIFIED: delete V1 verifier prompts/parser, add V2
  analysis/
    __init__.py              MODIFIED: remove PredictionVerifier export
    verifier.py              DELETE entirely

alembic/versions/
  <rev>_add_verification_metadata_v2.py    NEW

tests/
  test_models.py             MODIFIED: +2 tests
  test_llm_prompts.py        MODIFIED: -3 V1 verification tests, +12 V2 tests
  test_storage_postgres.py   MODIFIED: +2 mapper round-trip tests
  test_alembic.py            NEW: 1 migration sanity test
  test_analysis_verifier.py  DELETE entirely (4 tests)
```

---

## Task 1: V1 Clean Delete

Pure delete operation. After this task: `analysis/verifier.py` gone, V1 prompts/parser gone from prompts.py, V1 tests removed. Suite drops by 7 (4 verifier + 3 V1 prompt tests).

**Files:**
- Delete: `src/prophet_checker/analysis/verifier.py`
- Delete: `tests/test_analysis_verifier.py`
- Modify: `src/prophet_checker/analysis/__init__.py` (remove `PredictionVerifier`)
- Modify: `src/prophet_checker/llm/prompts.py` (remove V1 verification declarations)
- Modify: `tests/test_llm_prompts.py` (remove 3 V1 verification tests)

### Step 1: Delete verifier.py + test_analysis_verifier.py

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git rm src/prophet_checker/analysis/verifier.py
git rm tests/test_analysis_verifier.py
```

### Step 2: Update `src/prophet_checker/analysis/__init__.py`

Read current content. Replace:

```python
from prophet_checker.analysis.extractor import PredictionExtractor
from prophet_checker.analysis.verifier import PredictionVerifier

__all__ = ["PredictionExtractor", "PredictionVerifier"]
```

with:

```python
from prophet_checker.analysis.extractor import PredictionExtractor

__all__ = ["PredictionExtractor"]
```

### Step 3: Delete V1 verification declarations in `src/prophet_checker/llm/prompts.py`

Find and delete EXACTLY these blocks:

```python
VERIFICATION_SYSTEM = """You are a fact-checker who verifies predictions against known events.
You must provide evidence for your verdict. If you cannot find clear evidence, mark as unresolved.
Respond ONLY with raw JSON — do NOT wrap in markdown code fences (no ```json, no ``` wrappers)."""

VERIFICATION_TEMPLATE = """Verify the following prediction:

Claim: "{claim}"
Made on: {prediction_date}
Expected by: {target_date}

Determine if this prediction came true based on known events.

Respond with JSON:
{{
  "status": "confirmed" | "refuted" | "unresolved",
  "confidence": 0.0 to 1.0,
  "evidence_url": "URL to supporting evidence or null",
  "evidence_text": "Brief explanation of why this status was assigned"
}}"""
```

```python
def build_verification_prompt(claim: str, prediction_date: str, target_date: str | None) -> str:
    return VERIFICATION_TEMPLATE.format(
        claim=claim, prediction_date=prediction_date,
        target_date=target_date or "not specified",
    )
```

```python
def parse_verification_response(response: str) -> dict | None:
    try:
        data = json.loads(_strip_code_fence(response))
        if "status" in data and "confidence" in data:
            return data
        return None
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
```

```python
def get_verification_system() -> str:
    return VERIFICATION_SYSTEM
```

After deletion, `prompts.py` має лише: extraction + RAG declarations + helpers + `_strip_code_fence`. Verification — gone.

### Step 4: Delete V1 verification tests in `tests/test_llm_prompts.py`

Find lines 73-97 (3 V1 verification tests). Delete:

```python
def test_build_verification_prompt():
    prompt = build_verification_prompt(
        # ... full body ...
    )


def test_parse_verification_response_valid():
    # ... full body ...
    result = parse_verification_response(response)
    # ...


def test_parse_verification_response_invalid_json():
    result = parse_verification_response("broken json")
    # ...
```

Also delete the imports of removed functions from top of file:

```python
    build_verification_prompt,
    parse_verification_response,
```

### Step 5: Run pytest, verify reduced count

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: **116 passing** (123 − 7 deleted).

### Step 6: Verify imports still clean

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -c "
from prophet_checker.analysis import PredictionExtractor
from prophet_checker.llm.prompts import EXTRACTION_SYSTEM, parse_extraction_response
print('imports OK')
"
```

Expected: prints `imports OK`. No `ImportError` for `PredictionVerifier` (gone).

### Step 7: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add -A src/prophet_checker/analysis/__init__.py src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py
git commit -m "refactor(verifier): clean delete V1 verifier + prompts + tests (Task 19.5)"
```

---

## Task 2: Domain Model — `PredictionStrength` enum + 6 fields

**Files:**
- Modify: `src/prophet_checker/models/domain.py`
- Modify: `tests/test_models.py`

### Step 1: Append failing test to `tests/test_models.py`

```python
def test_prediction_strength_enum_values():
    from prophet_checker.models.domain import PredictionStrength
    assert PredictionStrength.LOW.value == "low"
    assert PredictionStrength.MEDIUM.value == "medium"
    assert PredictionStrength.HIGH.value == "high"


def test_prediction_has_v2_verification_field_defaults():
    from datetime import date
    from prophet_checker.models.domain import Prediction
    pred = Prediction(
        id="p1",
        document_id="d1",
        person_id="per1",
        claim_text="Test claim",
        prediction_date=date(2024, 1, 1),
    )
    assert pred.prediction_strength is None
    assert pred.max_horizon is None
    assert pred.next_check_at is None
    assert pred.verify_attempts == 0
    assert pred.last_verify_error is None
    assert pred.last_verify_error_at is None
```

### Step 2: Run new tests, expect FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/test_models.py::test_prediction_strength_enum_values tests/test_models.py::test_prediction_has_v2_verification_field_defaults -v
```

Expected: FAIL — `PredictionStrength` and new fields don't exist.

### Step 3: Update `src/prophet_checker/models/domain.py`

After existing `PredictionStatus` enum, add:

```python
class PredictionStrength(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
```

In `Prediction` class, find:

```python
class Prediction(BaseModel):
    id: str
    document_id: str
    person_id: str
    claim_text: str
    prediction_date: date
    target_date: date | None = None
    topic: str = ""
    status: PredictionStatus = PredictionStatus.UNRESOLVED
    confidence: float = 0.0
    evidence_url: str | None = None
    evidence_text: str | None = None
    verified_at: datetime | None = None
    embedding: list[float] | None = None
```

Append new fields after `embedding`:

```python
class Prediction(BaseModel):
    id: str
    document_id: str
    person_id: str
    claim_text: str
    prediction_date: date
    target_date: date | None = None
    topic: str = ""
    status: PredictionStatus = PredictionStatus.UNRESOLVED
    confidence: float = 0.0
    evidence_url: str | None = None
    evidence_text: str | None = None
    verified_at: datetime | None = None
    embedding: list[float] | None = None
    prediction_strength: PredictionStrength | None = None
    max_horizon: date | None = None
    next_check_at: date | None = None
    verify_attempts: int = 0
    last_verify_error: str | None = None
    last_verify_error_at: datetime | None = None
```

### Step 4: Run new tests, verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/test_models.py -v
```

Expected: всі test_models.py tests pass (existing + 2 нових).

### Step 5: Run full suite — no regression

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 118 passing (116 + 2 нових).

### Step 6: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/models/domain.py tests/test_models.py
git commit -m "feat(models): додаю PredictionStrength + 6 V2 verification fields (Task 19.5)"
```

---

## Task 3: DB Model — 6 columns + Index

**Files:**
- Modify: `src/prophet_checker/models/db.py`

### Step 1: Update `PredictionDB` class

In `src/prophet_checker/models/db.py`, find:

```python
class PredictionDB(Base):
    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("raw_documents.id"), nullable=False)
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    topic: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default="unresolved")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    embedding = mapped_column(Vector(1536), nullable=True)  # pgvector: 1536 dims = text-embedding-3-small

    document: Mapped[RawDocumentDB] = relationship(back_populates="predictions")
    person: Mapped[PersonDB] = relationship(back_populates="predictions")
```

Replace with (adds 6 columns + Index, keeps existing):

```python
class PredictionDB(Base):
    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("raw_documents.id"), nullable=False)
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    topic: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default="unresolved")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    evidence_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    embedding = mapped_column(Vector(1536), nullable=True)
    prediction_strength: Mapped[str | None] = mapped_column(String(10), nullable=True)
    max_horizon: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_check_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    verify_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default=func.now() and "0"
    )
    last_verify_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verify_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    document: Mapped[RawDocumentDB] = relationship(back_populates="predictions")
    person: Mapped[PersonDB] = relationship(back_populates="predictions")

    __table_args__ = (
        Index("idx_predictions_eligible", "verified_at", "next_check_at", "max_horizon"),
    )
```

**Note:** `verify_attempts` server_default — використовуємо raw text:

Replace `server_default=func.now() and "0"` rouge expression з clean SQL text:

```python
    verify_attempts: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default=text("0")
    )
```

Add `Integer`, `Index`, `text` to existing imports at top of `db.py`. Find existing import line:

```python
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
```

Replace with:

```python
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
```

### Step 2: Verify file syntactically valid

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -c "from prophet_checker.models.db import PredictionDB; print('OK:', PredictionDB.__tablename__)"
```

Expected: `OK: predictions`.

### Step 3: Run full suite — no regression

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 118 passing (no new tests, just schema additions).

### Step 4: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/models/db.py
git commit -m "feat(db): додаю 6 V2 verification columns + index на PredictionDB (Task 19.5)"
```

---

## Task 4: Mappers Round-Trip

**Files:**
- Modify: `src/prophet_checker/storage/postgres.py`
- Modify: `tests/test_storage_postgres.py`

### Step 1: Append 2 failing tests to `tests/test_storage_postgres.py`

Find existing test functions. Append at end of file:

```python
def test_domain_to_prediction_db_includes_v2_fields():
    from datetime import UTC, date, datetime
    from prophet_checker.models.domain import Prediction, PredictionStatus, PredictionStrength
    from prophet_checker.storage.postgres import domain_to_prediction_db

    pred = Prediction(
        id="p1",
        document_id="d1",
        person_id="per1",
        claim_text="Test claim",
        prediction_date=date(2024, 1, 1),
        prediction_strength=PredictionStrength.HIGH,
        max_horizon=date(2025, 1, 1),
        next_check_at=date(2024, 6, 1),
        verify_attempts=3,
        last_verify_error="ValueError: invalid status",
        last_verify_error_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    db_obj = domain_to_prediction_db(pred)
    assert db_obj.prediction_strength == "high"
    assert db_obj.max_horizon == date(2025, 1, 1)
    assert db_obj.next_check_at == date(2024, 6, 1)
    assert db_obj.verify_attempts == 3
    assert db_obj.last_verify_error == "ValueError: invalid status"
    assert db_obj.last_verify_error_at == datetime(2024, 5, 1, tzinfo=UTC)


def test_prediction_db_to_domain_includes_v2_fields():
    from datetime import UTC, date, datetime
    from prophet_checker.models.db import PredictionDB
    from prophet_checker.models.domain import PredictionStrength
    from prophet_checker.storage.postgres import prediction_db_to_domain

    db = PredictionDB(
        id="p1",
        document_id="d1",
        person_id="per1",
        claim_text="Test claim",
        prediction_date=date(2024, 1, 1),
        status="unresolved",
        confidence=0.0,
        prediction_strength="medium",
        max_horizon=date(2025, 1, 1),
        next_check_at=date(2024, 6, 1),
        verify_attempts=2,
        last_verify_error="JSONDecodeError",
        last_verify_error_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    pred = prediction_db_to_domain(db)
    assert pred.prediction_strength == PredictionStrength.MEDIUM
    assert pred.max_horizon == date(2025, 1, 1)
    assert pred.next_check_at == date(2024, 6, 1)
    assert pred.verify_attempts == 2
    assert pred.last_verify_error == "JSONDecodeError"
    assert pred.last_verify_error_at == datetime(2024, 5, 1, tzinfo=UTC)
```

### Step 2: Run new tests, expect FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/test_storage_postgres.py::test_domain_to_prediction_db_includes_v2_fields tests/test_storage_postgres.py::test_prediction_db_to_domain_includes_v2_fields -v
```

Expected: FAIL — mappers don't pass нові fields.

### Step 3: Update `domain_to_prediction_db` and `prediction_db_to_domain` in `src/prophet_checker/storage/postgres.py`

Find:

```python
def domain_to_prediction_db(pred: Prediction) -> PredictionDB:
    return PredictionDB(
        id=pred.id, document_id=pred.document_id, person_id=pred.person_id,
        claim_text=pred.claim_text, prediction_date=pred.prediction_date,
        target_date=pred.target_date, topic=pred.topic,
        status=pred.status.value, confidence=pred.confidence,
        evidence_url=pred.evidence_url, evidence_text=pred.evidence_text,
        verified_at=pred.verified_at, embedding=pred.embedding,
    )


def prediction_db_to_domain(db: PredictionDB) -> Prediction:
    return Prediction(
        id=db.id, document_id=db.document_id, person_id=db.person_id,
        claim_text=db.claim_text, prediction_date=db.prediction_date,
        target_date=db.target_date, topic=db.topic,
        status=PredictionStatus(db.status), confidence=db.confidence,
        evidence_url=db.evidence_url, evidence_text=db.evidence_text,
        verified_at=db.verified_at,
    )
```

Replace with:

```python
def domain_to_prediction_db(pred: Prediction) -> PredictionDB:
    return PredictionDB(
        id=pred.id, document_id=pred.document_id, person_id=pred.person_id,
        claim_text=pred.claim_text, prediction_date=pred.prediction_date,
        target_date=pred.target_date, topic=pred.topic,
        status=pred.status.value, confidence=pred.confidence,
        evidence_url=pred.evidence_url, evidence_text=pred.evidence_text,
        verified_at=pred.verified_at, embedding=pred.embedding,
        prediction_strength=pred.prediction_strength.value if pred.prediction_strength else None,
        max_horizon=pred.max_horizon,
        next_check_at=pred.next_check_at,
        verify_attempts=pred.verify_attempts,
        last_verify_error=pred.last_verify_error,
        last_verify_error_at=pred.last_verify_error_at,
    )


def prediction_db_to_domain(db: PredictionDB) -> Prediction:
    return Prediction(
        id=db.id, document_id=db.document_id, person_id=db.person_id,
        claim_text=db.claim_text, prediction_date=db.prediction_date,
        target_date=db.target_date, topic=db.topic,
        status=PredictionStatus(db.status), confidence=db.confidence,
        evidence_url=db.evidence_url, evidence_text=db.evidence_text,
        verified_at=db.verified_at,
        prediction_strength=PredictionStrength(db.prediction_strength) if db.prediction_strength else None,
        max_horizon=db.max_horizon,
        next_check_at=db.next_check_at,
        verify_attempts=db.verify_attempts,
        last_verify_error=db.last_verify_error,
        last_verify_error_at=db.last_verify_error_at,
    )
```

Add `PredictionStrength` to existing import:

```python
from prophet_checker.models.domain import (
    Person, PersonSource, Prediction, PredictionStatus, PredictionStrength, RawDocument, SourceType,
)
```

### Step 4: Run tests — verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/test_storage_postgres.py -v
```

Expected: all (existing + 2 new) pass.

### Step 5: Run full suite

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 120 passing (118 + 2 нових).

### Step 6: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/storage/postgres.py tests/test_storage_postgres.py
git commit -m "feat(storage): mappers round-trip 6 V2 verification fields (Task 19.5)"
```

---

## Task 5: V2 Prompts + Parser

This is the largest task — 12 tests + significant impl. Subdivide into slices.

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py`
- Modify: `tests/test_llm_prompts.py`

### Slice 5a: Prompt build + happy path tests

#### Step 1: Append `VERIFICATION_SYSTEM_V2` + `VERIFICATION_TEMPLATE_V2` to `src/prophet_checker/llm/prompts.py`

After existing `RAG_TEMPLATE` declaration, append:

```python
VERIFICATION_SYSTEM_V2 = """You are a fact-checker who verifies political/economic predictions about Ukraine
and global events. Today's date is {today}. The prediction was made on a past
date — your job is to assess whether it can be evaluated NOW, and if so, what
the verdict is.

Determine FOUR outputs:

═══════════════════════════════════════════════════════════════════
1) STATUS — exactly one of:

   "confirmed" — the predicted event happened as foretold. You have
                concrete evidence. The prediction's timeframe (target_date,
                or reasonable interpretation) has passed.

   "refuted"  — the predicted event did NOT happen, OR the opposite occurred.
                Concrete evidence required. Timeframe has passed.

   "unresolved" — the predicted event's timeframe has passed, but evidence is
                  ambiguous, the claim is too vague to falsify, or no public
                  record exists. Re-checking later WON'T help — this is a
                  permanent verdict.

   "premature" — the predicted event has not yet occurred but is still
                 POSSIBLE. The timeframe hasn't elapsed, OR the trigger
                 condition (for conditional predictions like "if X happens")
                 hasn't fired. We should retry verification later.

═══════════════════════════════════════════════════════════════════
2) PREDICTION_STRENGTH — assess the CLAIM ITSELF (independent of outcome):

   "high"   — concrete falsifiable claim with measurable outcome.
   "medium" — probabilistic but substantive claim with clear outcome.
   "low"    — vague hedge, possibility statement, or non-substantive forecast.

═══════════════════════════════════════════════════════════════════
3) MAX_HORIZON — latest reasonable date to keep checking this prediction.
   Set ONLY if status="premature" AND target_date is null. Otherwise null.

═══════════════════════════════════════════════════════════════════
4) RETRY_AFTER — only when status="premature". When does it make sense to
   re-evaluate?

═══════════════════════════════════════════════════════════════════
MUTUAL EXCLUSION RULES (strictly enforce):
- status=confirmed/refuted → evidence MUST be a concrete fact, retry_after=null
- status=unresolved → retry_after=null (recheck won't help)
- status=premature → retry_after MUST be a date, evidence may be null
- max_horizon set ONLY when status=premature AND target_date=null

Respond ONLY with raw JSON, no markdown fences:

{{
  "status": "confirmed" | "refuted" | "unresolved" | "premature",
  "confidence": 0.0 to 1.0,
  "prediction_strength": "low" | "medium" | "high",
  "reasoning": "1-3 sentences explaining the verdict and strength",
  "evidence": "concrete fact text or null. Do NOT include URLs (you have no web access).",
  "retry_after": "YYYY-MM-DD or null",
  "max_horizon": "YYYY-MM-DD or null"
}}"""


VERIFICATION_TEMPLATE_V2 = """Claim: "{claim}"
Made on: {prediction_date}
Expected by: {target_date}

Original post excerpt (for context):
---
{post_excerpt}
---

Today: {today}.

Provide your verdict per the rubric."""
```

#### Step 2: Append builders to `prompts.py`

After existing `build_rag_prompt`, append:

```python
def build_verification_prompt_v2(
    claim: str,
    prediction_date: str,
    target_date: str | None,
    today: str,
    post_excerpt: str,
) -> str:
    return VERIFICATION_TEMPLATE_V2.format(
        claim=claim,
        prediction_date=prediction_date,
        target_date=target_date or "not specified",
        today=today,
        post_excerpt=post_excerpt,
    )


def get_verification_system_v2(today: str) -> str:
    return VERIFICATION_SYSTEM_V2.format(today=today)
```

#### Step 3: Append happy path test + prompt build test до `tests/test_llm_prompts.py`

```python
def test_build_verification_prompt_v2_substitutes_all_fields():
    from prophet_checker.llm.prompts import build_verification_prompt_v2

    prompt = build_verification_prompt_v2(
        claim="Test claim",
        prediction_date="2024-01-01",
        target_date="2024-12-31",
        today="2025-01-15",
        post_excerpt="Original post text",
    )
    assert "Test claim" in prompt
    assert "2024-01-01" in prompt
    assert "2024-12-31" in prompt
    assert "2025-01-15" in prompt
    assert "Original post text" in prompt
```

#### Step 4: Run tests, verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/test_llm_prompts.py -v
```

Expected: all pass (existing + 1 new).

#### Step 5: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py
git commit -m "feat(prompts): додаю VERIFICATION_SYSTEM_V2 + builders (Task 19.5 slice 5a)"
```

### Slice 5b: V2 parser — happy path

#### Step 1: Append parser to `prompts.py`

After existing `parse_extraction_response`, append:

```python
def parse_verification_response_v2(response: str) -> dict:
    data = json.loads(_strip_code_fence(response))

    required = {"status", "confidence", "prediction_strength", "reasoning"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"missing required field: {sorted(missing)[0]}")

    if data["status"] not in {"confirmed", "refuted", "unresolved", "premature"}:
        raise ValueError(
            f"invalid status: {data['status']!r} "
            f"(expected confirmed/refuted/unresolved/premature)"
        )

    if data["prediction_strength"] not in {"low", "medium", "high"}:
        raise ValueError(
            f"invalid prediction_strength: {data['prediction_strength']!r} "
            f"(expected low/medium/high)"
        )

    status = data["status"]
    retry_after = data.get("retry_after")
    max_horizon = data.get("max_horizon")
    evidence = data.get("evidence") or None

    if status == "premature" and retry_after is None:
        raise ValueError("status=premature requires retry_after")

    if status in {"confirmed", "refuted"} and not evidence:
        raise ValueError(f"status={status} requires evidence")

    if status != "premature" and retry_after is not None:
        logger.warning(
            "soft-normalize: dropping extraneous retry_after on status=%s", status
        )
        data["retry_after"] = None

    if status != "premature" and max_horizon is not None:
        logger.warning(
            "soft-normalize: dropping extraneous max_horizon on status=%s", status
        )
        data["max_horizon"] = None

    data["evidence"] = evidence
    return data
```

Add `logger` to top of `prompts.py`. Find:

```python
import json
import re
```

Replace with:

```python
import json
import logging
import re

logger = logging.getLogger(__name__)
```

#### Step 2: Append 2 happy path tests до `tests/test_llm_prompts.py`

```python
def test_parse_verification_response_v2_terminal_confirmed():
    from prophet_checker.llm.prompts import parse_verification_response_v2

    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "high",
        "reasoning": "Event occurred as predicted in June 2023.",
        "evidence": "Counteroffensive started June 2023 per Reuters.",
        "retry_after": null,
        "max_horizon": null
    }"""
    result = parse_verification_response_v2(response)
    assert result["status"] == "confirmed"
    assert result["prediction_strength"] == "high"
    assert result["evidence"] == "Counteroffensive started June 2023 per Reuters."


def test_parse_verification_response_v2_premature():
    from prophet_checker.llm.prompts import parse_verification_response_v2

    response = """{
        "status": "premature",
        "confidence": 0.5,
        "prediction_strength": "medium",
        "reasoning": "Trump's term started recently — too early to assess.",
        "evidence": null,
        "retry_after": "2025-06-01",
        "max_horizon": "2028-01-01"
    }"""
    result = parse_verification_response_v2(response)
    assert result["status"] == "premature"
    assert result["retry_after"] == "2025-06-01"
    assert result["max_horizon"] == "2028-01-01"
```

#### Step 3: Run tests, verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/test_llm_prompts.py -v
```

Expected: all pass (existing + 2 new = 3 V2 tests so far).

#### Step 4: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py
git commit -m "feat(prompts): parse_verification_response_v2 + happy path tests (Task 19.5 slice 5b)"
```

### Slice 5c: V2 parser — hard-reject tests

#### Step 1: Append 6 hard-reject tests

```python
def test_parse_v2_raises_on_invalid_json():
    import json
    from prophet_checker.llm.prompts import parse_verification_response_v2
    with pytest.raises(json.JSONDecodeError):
        parse_verification_response_v2("not valid json")


def test_parse_v2_raises_on_missing_required_field():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = '{"status": "confirmed", "confidence": 0.9, "evidence": "fact"}'
    with pytest.raises(ValueError, match="missing required field"):
        parse_verification_response_v2(response)


def test_parse_v2_raises_on_invalid_status():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "verified",
        "confidence": 0.9,
        "prediction_strength": "high",
        "reasoning": "...",
        "evidence": "fact"
    }"""
    with pytest.raises(ValueError, match="invalid status"):
        parse_verification_response_v2(response)


def test_parse_v2_raises_on_invalid_prediction_strength():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "strong",
        "reasoning": "...",
        "evidence": "fact"
    }"""
    with pytest.raises(ValueError, match="invalid prediction_strength"):
        parse_verification_response_v2(response)


def test_parse_v2_raises_premature_without_retry_after():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "premature",
        "confidence": 0.5,
        "prediction_strength": "medium",
        "reasoning": "...",
        "evidence": null,
        "retry_after": null
    }"""
    with pytest.raises(ValueError, match="premature requires retry_after"):
        parse_verification_response_v2(response)


def test_parse_v2_raises_terminal_without_evidence():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "high",
        "reasoning": "...",
        "evidence": null
    }"""
    with pytest.raises(ValueError, match="confirmed requires evidence"):
        parse_verification_response_v2(response)
```

Verify `pytest` is imported at top of `tests/test_llm_prompts.py` (likely already). If not:

```python
import pytest
```

#### Step 2: Run tests, verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/test_llm_prompts.py -v
```

Expected: all pass.

#### Step 3: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add tests/test_llm_prompts.py
git commit -m "test(prompts): 6 hard-reject parser tests (Task 19.5 slice 5c)"
```

### Slice 5d: V2 parser — soft-normalize tests

#### Step 1: Append 3 soft-normalize tests

```python
def test_parse_v2_drops_extraneous_retry_after_on_terminal():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "high",
        "reasoning": "Event occurred.",
        "evidence": "concrete fact",
        "retry_after": "2025-06-01"
    }"""
    result = parse_verification_response_v2(response)
    assert result["status"] == "confirmed"
    assert result["retry_after"] is None
    assert result["evidence"] == "concrete fact"


def test_parse_v2_drops_extraneous_retry_after_on_unresolved():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "unresolved",
        "confidence": 0.4,
        "prediction_strength": "low",
        "reasoning": "Too vague.",
        "evidence": null,
        "retry_after": "2025-06-01"
    }"""
    result = parse_verification_response_v2(response)
    assert result["status"] == "unresolved"
    assert result["retry_after"] is None


def test_parse_v2_drops_extraneous_max_horizon_on_non_premature():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "high",
        "reasoning": "Event occurred.",
        "evidence": "concrete fact",
        "max_horizon": "2028-01-01"
    }"""
    result = parse_verification_response_v2(response)
    assert result["status"] == "confirmed"
    assert result["max_horizon"] is None
```

#### Step 2: Run tests, verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/test_llm_prompts.py -v
```

Expected: all pass.

#### Step 3: Run full suite

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 132 passing (120 + 12 V2 prompt/parser tests).

#### Step 4: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add tests/test_llm_prompts.py
git commit -m "test(prompts): 3 soft-normalize parser tests (Task 19.5 slice 5d)"
```

---

## Task 6: Alembic Migration

**Files:**
- Create: `alembic/versions/<rev>_add_verification_metadata_v2.py`
- Create: `tests/test_alembic.py`

### Step 1: Generate migration scaffold

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/alembic revision --autogenerate -m "add verification metadata v2"
```

Expected: prints `Generating ... new revision ID ... done`. Note the generated file path.

### Step 2: Verify autogenerate detected schema diff

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
ls alembic/versions/
```

Should be 2 migration files: `edb2e385f26b_initial_schema_with_pgvector.py` + new `<rev>_add_verification_metadata_v2.py`.

### Step 3: Inspect + edit generated migration

Open new migration file. The autogenerated `upgrade()` should have:
- 6 `op.add_column` calls
- 1 `op.create_index` for `idx_predictions_eligible`

If autogenerate has issues (missing server_default for verify_attempts, or wrong column types), manually replace `upgrade()` to be:

```python
def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("prediction_strength", sa.String(length=10), nullable=True),
    )
    op.add_column(
        "predictions",
        sa.Column("max_horizon", sa.Date(), nullable=True),
    )
    op.add_column(
        "predictions",
        sa.Column("next_check_at", sa.Date(), nullable=True),
    )
    op.add_column(
        "predictions",
        sa.Column(
            "verify_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "predictions",
        sa.Column("last_verify_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "predictions",
        sa.Column("last_verify_error_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_predictions_eligible",
        "predictions",
        ["verified_at", "next_check_at", "max_horizon"],
    )


def downgrade() -> None:
    op.drop_index("idx_predictions_eligible", table_name="predictions")
    op.drop_column("predictions", "last_verify_error_at")
    op.drop_column("predictions", "last_verify_error")
    op.drop_column("predictions", "verify_attempts")
    op.drop_column("predictions", "next_check_at")
    op.drop_column("predictions", "max_horizon")
    op.drop_column("predictions", "prediction_strength")
```

Verify top of migration has correct `down_revision = "edb2e385f26b"` (Task 17 baseline).

### Step 4: Create `tests/test_alembic.py`

```python
import importlib.util
import pathlib


def test_v2_migration_loads_cleanly():
    versions = pathlib.Path("alembic/versions")
    files = list(versions.glob("*add_verification_metadata_v2*"))
    assert len(files) == 1, f"expected 1 migration file, got {files}"

    spec = importlib.util.spec_from_file_location("v2_migration", files[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
    assert hasattr(module, "revision")
    assert module.down_revision == "edb2e385f26b"
```

### Step 5: Run new test, verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/test_alembic.py -v
```

Expected: PASS.

### Step 6: Run full suite

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -q
```

Expected: 133 passing (132 + 1 new).

### Step 7: Manual smoke — apply migration on Docker postgres (optional but recommended)

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
docker compose up -d
sleep 5
.venv/bin/alembic upgrade head
docker exec prophet_postgres psql -U prophet -d prophet_checker -c "\d predictions" | head -30
```

Expected: predictions table має 6 нових columns + index `idx_predictions_eligible`. `verify_attempts` shows `not null default 0`.

If Docker unavailable, skip — test_alembic.py sanity check sufficient для CI-style verification.

### Step 8: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git add alembic/versions/ tests/test_alembic.py
git commit -m "feat(alembic): міграція add_verification_metadata_v2 + sanity test (Task 19.5)"
```

---

## Final verification

### Step 1: Run full test suite

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -m pytest tests/ -v
```

Expected: 133 tests passing. Specifically:
- 123 baseline (post-Task 19)
- −7 V1 deletes (4 verifier + 3 V1 prompt tests)
- +2 domain tests
- +2 mapper round-trip tests
- +12 V2 prompt/parser tests (1 prompt build + 2 happy + 6 hard-reject + 3 soft-normalize)
- +1 migration sanity
- = **133**

### Step 2: Verify imports

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -c "
from prophet_checker.models.domain import Prediction, PredictionStrength
from prophet_checker.models.db import PredictionDB
from prophet_checker.llm.prompts import (
    VERIFICATION_SYSTEM_V2,
    VERIFICATION_TEMPLATE_V2,
    build_verification_prompt_v2,
    get_verification_system_v2,
    parse_verification_response_v2,
)
print('OK: V2 imports clean')

# verify V1 truly gone
try:
    from prophet_checker.llm.prompts import parse_verification_response
    print('FAIL: V1 parser still importable')
except ImportError:
    print('OK: V1 deleted')
"
```

Expected:
```
OK: V2 imports clean
OK: V1 deleted
```

### Step 3: Verify schema fields exist

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
.venv/bin/python -c "
from prophet_checker.models.db import PredictionDB
required = {
    'prediction_strength', 'max_horizon', 'next_check_at',
    'verify_attempts', 'last_verify_error', 'last_verify_error_at'
}
columns = {c.name for c in PredictionDB.__table__.columns}
missing = required - columns
assert not missing, f'missing columns: {missing}'
print('OK: 6 V2 columns present')

indexes = {idx.name for idx in PredictionDB.__table__.indexes}
assert 'idx_predictions_eligible' in indexes, f'index missing: got {indexes}'
print('OK: idx_predictions_eligible present')
"
```

Expected:
```
OK: 6 V2 columns present
OK: idx_predictions_eligible present
```

### Step 4: Verify V1 truly deleted

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
ls src/prophet_checker/analysis/ | grep verifier
```

Expected: empty output (no `verifier.py`).

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
ls tests/ | grep verifier
```

Expected: empty output (no `test_analysis_verifier.py`).

### Step 5: Verify git log

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker
git log --oneline -10
```

Expected: 6 task commits (V1 delete → domain → DB → mappers → V2 prompts/parser slices → migration).

---

## Out of Scope (deferred to Task 20)

- ❌ V2 verifier class (`PredictionVerifier.verify_v2`)
- ❌ Repo methods (`get_eligible_for_verification`, `force_unresolved_past_horizon`)
- ❌ HTTP endpoint `POST /verify/run`
- ❌ Settings additions для verifier
- ❌ Real DB integration tests (manual smoke sufficient)

---

## Cross-references

- **Spec:** [`2026-05-07-task-19-5-schema-prompts-design.md`](2026-05-07-task-19-5-schema-prompts-design.md)
- **Authoritative v2 design:** [`2026-04-26-verification-trigger-policy-design.md`](2026-04-26-verification-trigger-policy-design.md)
- **Decomposition:** [`2026-05-07-verifier-v2-decomposition.md`](2026-05-07-verifier-v2-decomposition.md)
- **Pattern source (clean delete V1):** [`../ingestion-to-aws/2026-05-01-llm-client-split-design.md`](../ingestion-to-aws/2026-05-01-llm-client-split-design.md)
- **Pattern source (migration):** [`../ingestion-to-aws/2026-05-07-docker-compose-design.md`](../ingestion-to-aws/2026-05-07-docker-compose-design.md)
