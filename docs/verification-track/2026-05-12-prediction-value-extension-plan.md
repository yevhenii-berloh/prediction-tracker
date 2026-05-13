# PredictionValue Extension — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or execute inline (patterns established by Task 19.5). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `PredictionValue` enum field (low/medium/high) як 8th output до V2 verifier prompt + повна schema integration + retrofit gold dataset + resume labeling.

**Background:** Surfaced під час Task 19.7a manual labeling (20/35 entries done). User identified missing dimension — `strength` measures claim formulation quality, але event IMPORTANCE/RESONANCE is independent dimension. Need separate `value` enum.

**Architecture:** Mirrors Task 19.5 pattern. Single new field across domain → DB → mapper → prompt → parser → migration. Plus gold dataset retrofit та resume labeling.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy 2.0 async, Alembic, pytest-asyncio.

**Test count delta:** +5 new (2 domain + 2 mapper + 1 hard-reject parser + 1 migration sanity)... wait, recompute:
- +2 domain (enum + field default)
- +2 mapper (round-trip)
- +1 parser hard-reject (invalid value enum)
- +1 migration sanity
- = **+6 new tests**
- Updated: ~5 existing parser/test cases (now include prediction_value у JSON fixtures)

Current 133 → **139** після extension.

---

## File Structure (locked-in)

```
src/prophet_checker/
  models/
    domain.py                MODIFIED: add PredictionValue enum + Prediction.prediction_value field
    db.py                    MODIFIED: add PredictionDB.prediction_value column
  storage/
    postgres.py              MODIFIED: mapper round-trip
  llm/
    prompts.py               MODIFIED: V2 prompt SEVEN → EIGHT outputs, parser validates new enum

alembic/versions/
  <rev>_add_prediction_value.py    NEW

tests/
  test_models.py             MODIFIED: +2 tests
  test_llm_prompts.py        MODIFIED: +1 hard-reject test, update existing happy-path JSON fixtures
  test_storage_postgres.py   MODIFIED: +2 mapper tests
  test_alembic.py            MODIFIED: +1 sanity test for new migration (OR extend existing)

scripts/
  outputs/verification_eval/
    _partial_labels.json     MODIFIED: retrofit 20 entries з expected_value
    _working_candidates.json (unchanged)
  data/
    verification_gold_labels.json   NEW (final commit after labeling complete)
```

---

## Phase A: V2 Schema Extension

Mirrors Task 19.5 pattern (add 1 field).

### Task A1: Domain — PredictionValue enum + field

**Files:**
- Modify: `src/prophet_checker/models/domain.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Append failing tests**

```python
def test_prediction_value_enum_values():
    from prophet_checker.models.domain import PredictionValue
    assert PredictionValue.LOW.value == "low"
    assert PredictionValue.MEDIUM.value == "medium"
    assert PredictionValue.HIGH.value == "high"


def test_prediction_has_value_field_default():
    from datetime import date
    from prophet_checker.models.domain import Prediction
    pred = Prediction(
        id="p1", document_id="d1", person_id="per1",
        claim_text="Test", prediction_date=date(2024, 1, 1),
    )
    assert pred.prediction_value is None
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Update domain.py**

After `PredictionStrength` enum, append:

```python
class PredictionValue(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
```

In `Prediction` class, after `prediction_strength` field, add:

```python
    prediction_value: PredictionValue | None = None
```

- [ ] **Step 4: Run, verify PASS**

- [ ] **Step 5: Run full suite — 135 passing**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(models): додаю PredictionValue enum + field (extension)"
```

### Task A2: DB model column

**Files:** Modify `src/prophet_checker/models/db.py`

- [ ] **Step 1: Update PredictionDB**

In `PredictionDB`, after `prediction_strength` column, add:

```python
    prediction_value: Mapped[str | None] = mapped_column(String(10), nullable=True)
```

- [ ] **Step 2: Verify schema loads**

```bash
.venv/bin/python -c "
from prophet_checker.models.db import PredictionDB
cols = {c.name for c in PredictionDB.__table__.columns}
assert 'prediction_value' in cols
print('OK')
"
```

- [ ] **Step 3: pytest 135 passing**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(db): додаю prediction_value column на PredictionDB"
```

### Task A3: Mappers round-trip

**Files:**
- Modify: `src/prophet_checker/storage/postgres.py`
- Modify: `tests/test_storage_postgres.py`

- [ ] **Step 1: Append 2 failing tests**

```python
def test_domain_to_prediction_db_includes_prediction_value():
    from datetime import date
    from prophet_checker.models.domain import Prediction, PredictionValue
    from prophet_checker.storage.postgres import domain_to_prediction_db

    pred = Prediction(
        id="p1", document_id="d1", person_id="per1",
        claim_text="Test", prediction_date=date(2024, 1, 1),
        prediction_value=PredictionValue.HIGH,
    )
    db_obj = domain_to_prediction_db(pred)
    assert db_obj.prediction_value == "high"


def test_prediction_db_to_domain_includes_prediction_value():
    from datetime import date
    from prophet_checker.models.db import PredictionDB
    from prophet_checker.models.domain import PredictionValue
    from prophet_checker.storage.postgres import prediction_db_to_domain

    db = PredictionDB(
        id="p1", document_id="d1", person_id="per1",
        claim_text="Test", prediction_date=date(2024, 1, 1),
        status="unresolved", confidence=0.0,
        prediction_value="medium",
    )
    pred = prediction_db_to_domain(db)
    assert pred.prediction_value == PredictionValue.MEDIUM
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Update mappers in postgres.py**

Add `PredictionValue` to import:

```python
from prophet_checker.models.domain import (
    Person, PersonSource, Prediction, PredictionStatus, PredictionStrength, PredictionValue, RawDocument, SourceType,
)
```

In `domain_to_prediction_db`, append:

```python
        prediction_value=pred.prediction_value.value if pred.prediction_value else None,
```

In `prediction_db_to_domain`, append:

```python
        prediction_value=PredictionValue(db.prediction_value) if db.prediction_value else None,
```

- [ ] **Step 4: Run tests pass**

- [ ] **Step 5: pytest 137 passing**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(storage): mapper round-trip prediction_value field"
```

### Task A4: V2 prompt SEVEN → EIGHT outputs

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py`

- [ ] **Step 1: Update VERIFICATION_SYSTEM_V2**

Find:

```
Determine SEVEN outputs (all required in JSON response):
```

Replace with:

```
Determine EIGHT outputs (all required in JSON response):
```

After existing field 7 (max_horizon), insert new field 8:

```
8) prediction_value — assess REAL-WORLD IMPORTANCE of the predicted event (independent of formulation):

   "high"   — war/peace outcomes, major political transitions (regime change, presidential elections),
              large-scale economic disruptions (currency crisis, mass sanctions), events з significant
              lives-at-stake / civilizational consequences.

   "medium" — notable diplomatic events (major summits, treaty signings), sector-specific economic
              impacts, regional political shifts, specific military operation outcomes без strategic
              war shift, policy decisions з measurable but bounded impact.

   "low"    — routine meetings, procedural events (regular elections without regime change, scheduled
              summits), niche/peripheral topics, internal political maneuvers без direct external
              stakes, mundane forecasts.
```

Update JSON schema example at end of prompt:

```json
{
  "status": "...",
  "confidence": 0.0,
  "prediction_strength": "...",
  "reasoning": "...",
  "evidence": "..." | null,
  "retry_after": "YYYY-MM-DD" | null,
  "max_horizon": "YYYY-MM-DD" | null,
  "prediction_value": "low" | "medium" | "high"
}
```

- [ ] **Step 2: Update parse_verification_response_v2**

Add to required fields:

```python
    required = {"status", "confidence", "prediction_strength", "reasoning", "prediction_value"}
```

Add enum validation after prediction_strength validation:

```python
    if data["prediction_value"] not in {"low", "medium", "high"}:
        raise ValueError(
            f"invalid prediction_value: {data['prediction_value']!r} "
            f"(expected low/medium/high)"
        )
```

- [ ] **Step 3: Update existing parser tests**

Update happy-path test fixtures у `tests/test_llm_prompts.py` to include `"prediction_value": "high"` (or appropriate value) у JSON response strings. Affects ~5 existing tests (happy paths + soft-normalize tests):
- `test_parse_verification_response_v2_terminal_confirmed`
- `test_parse_verification_response_v2_premature`
- `test_parse_v2_drops_extraneous_retry_after_on_terminal`
- `test_parse_v2_drops_extraneous_retry_after_on_unresolved`
- `test_parse_v2_drops_extraneous_max_horizon_on_non_premature`

For each, add `"prediction_value": "low"` (or appropriate) to JSON fixture.

Hard-reject tests (`test_parse_v2_raises_on_missing_required_field`, etc.) may need update — some currently send incomplete JSON that just happens to fail on different field. Re-verify each.

- [ ] **Step 4: Add new hard-reject test for invalid value**

```python
def test_parse_v2_raises_on_invalid_prediction_value():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "high",
        "reasoning": "...",
        "evidence": "fact",
        "prediction_value": "critical"
    }"""
    with pytest.raises(ValueError, match="invalid prediction_value"):
        parse_verification_response_v2(response)
```

- [ ] **Step 5: pytest всі pass (138 з 1 новим test)**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(prompts): V2 8th output prediction_value + parser validation + tests"
```

### Task A5: Alembic migration

**Files:**
- Create: `alembic/versions/<rev>_add_prediction_value.py`
- Modify: `tests/test_alembic.py` (extend existing OR add second test function)

- [ ] **Step 1: Generate migration scaffold**

```bash
.venv/bin/alembic revision --autogenerate -m "add prediction_value column"
```

- [ ] **Step 2: Edit generated migration**

Replace `upgrade()`:

```python
def upgrade() -> None:
    op.add_column(
        "predictions",
        sa.Column("prediction_value", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("predictions", "prediction_value")
```

Verify `down_revision = "<previous head>"` (likely `30fd925789cb` from Task 19.5).

- [ ] **Step 3: Extend `tests/test_alembic.py`**

Add second test function:

```python
def test_prediction_value_migration_loads_cleanly():
    versions = pathlib.Path("alembic/versions")
    files = list(versions.glob("*add_prediction_value*"))
    assert len(files) == 1
    spec = importlib.util.spec_from_file_location("m", files[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "upgrade")
    assert hasattr(module, "downgrade")
    assert hasattr(module, "revision")
    assert module.down_revision == "30fd925789cb"
```

- [ ] **Step 4: pytest 139 passing**

- [ ] **Step 5: Manual smoke (optional — якщо Docker available)**

```bash
docker compose up -d && sleep 5
.venv/bin/alembic upgrade head
docker exec prophet_postgres psql -U prophet -d prophet_checker -c "\d predictions" | grep prediction_value
docker compose down
```

Expect: `prediction_value | character varying(10) | nullable`

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(alembic): міграція add_prediction_value + sanity test"
```

---

## Phase B: Gold dataset retrofit + resume labeling

### Task B1: Retrofit 20 done entries

Each existing gold entry needs `expected_value` judgment. Inline в chat — fast pass через 20 entries.

- [ ] **Step 1: For each entry 1-20:**
  - I propose `expected_value` based on claim's real-world stakes
  - User confirms `a` / corrects `e <value>`
  - Update entry у `_partial_labels.json`

- [ ] **Step 2: Verify all 20 entries have `expected_value`**

### Task B2: Resume labeling entries 21-35

- [ ] **Step 1: Continue interactive labeling з entries 21-35**
  - My proposal now includes `expected_value` як 7th field у table
  - Same `a`/`e`/`s` workflow

- [ ] **Step 2: All 35 entries complete**

### Task B3: Write final gold file + commit

- [ ] **Step 1: Rename `_partial_labels.json` → `scripts/data/verification_gold_labels.json`**

- [ ] **Step 2: Add metadata + ensure schema consistency**

Final schema per entry:

```json
{
  "id": "tg:O_Arestovich_official_XXX:N",
  "post_id": "...",
  "claim_text": "...",
  "prediction_date": "...",
  "target_date": "..." | null,
  "post_excerpt": "...",
  "expected_status": "...",
  "expected_confidence": 0.0,
  "expected_strength": "...",
  "expected_value": "...",            // NEW
  "expected_evidence": "..." | null,
  "expected_retry_after": "..." | null,
  "expected_max_horizon": "..." | null,
  "reviewer_notes": "..."
}
```

- [ ] **Step 3: Commit gold labels**

```bash
git add scripts/data/verification_gold_labels.json
git commit -m "data: 35 verification gold labels з prediction_value (Task 19.7a)"
```

### Task B4: Update Task 19.7a spec doc

- [ ] **Step 1: Add note to** `docs/verification-track/2026-05-12-task-19-7a-gold-labeling-design.md`

Add section: "PredictionValue extension landed mid-execution. See plan: 2026-05-12-prediction-value-extension-plan.md"

---

## Out of Scope

- ❌ Multi-model evaluation (Task 19.7b)
- ❌ VerificationOrchestrator (Task 20)
- ❌ Settings additions (deferred)
- ❌ Production verifier consuming `prediction_value` для logic decisions (Task 20 may use)

---

## Cross-references

- **Task 19.5 (foundations):** [`2026-05-07-task-19-5-schema-prompts-design.md`](2026-05-07-task-19-5-schema-prompts-design.md)
- **Task 19.7a (gold labeling):** [`2026-05-12-task-19-7a-gold-labeling-design.md`](2026-05-12-task-19-7a-gold-labeling-design.md)
- **Decomposition:** [`2026-05-07-verifier-v2-decomposition.md`](2026-05-07-verifier-v2-decomposition.md)
- **Authoritative V2 spec:** [`../verifier-v2/2026-04-26-verification-trigger-policy-design.md`](../verifier-v2/2026-04-26-verification-trigger-policy-design.md)
