# Task 19.5 — Schema + V2 Prompts Foundation Design

**Status:** approved 2026-05-10
**Task:** 19.5 (verifier-v2 decomposition) — first sub-task; foundations only
**Prerequisites:** ✅ Task 17 (baseline migration `edb2e385f26b`)
**Next:** Task 19.7 (verification model evaluation), потім Task 20 (orchestrator)

**Authoritative spec (still valid):** [`2026-04-26-verification-trigger-policy-design.md`](2026-04-26-verification-trigger-policy-design.md)
**Decomposition strategy:** [`2026-05-07-verifier-v2-decomposition.md`](2026-05-07-verifier-v2-decomposition.md)

---

## TL;DR

Foundation для V2 verifier: schema columns + V2 prompts/parser. Не реалізує orchestrator або new verifier class — це Task 20.

**Clean delete V1.** V1 verifier idle (no production callers); видаляємо повністю замість paralleling.

**Schema:** 6 нових columns (4 from spec + 2 error tracking) + 1 index.

**Parser:** raises `ValueError` on contract violations (replaces "return None" pattern). Soft-normalizes extraneous fields.

**Test count:** 123 → 133 (+10 net: +17 new, −7 V1 deletes).

---

## Architectural Decisions (Q1–Q10)

| # | Decision | Rationale |
|---|----------|-----------|
| Q1 | **V1 clean delete** (not paralleled) | V1 has no production callers (idle since inception). Deprecation phase = ceremony without value. Delete now: simpler maintenance, fewer migration paths. |
| Q5 | **6 нових fields** (4 spec + 2 error tracking) | Per [Q5d revision]: `verify_attempts` increments only on SUCCESSFUL verify (not error). Errors tracked via separate `last_verify_error` (text) + `last_verify_error_at` (timestamp) columns. Cleared on next success. |
| Q6 | **B-tree index** `(verified_at, next_check_at, max_horizon)` | Matches `get_eligible_for_verification` SQL filter. `verified_at` first (most selective). Non-partial — pet-friendly, simpler. |
| Q7 | **Hard-reject parser cases raise `ValueError`** | Loud errors (vs silent None). Catches contract violations: missing required, invalid enums, premature без retry_after, C/R без evidence. Orchestrator catches uniformly. |
| Q8 | **Soft-normalize parser cases**: drop extraneous fields | Verdict still trustworthy when LLM adds extra fields (retry_after on terminal, max_horizon on non-premature). Drop, log warning, keep result. |
| Q-extra | **Evidence text-only (no URLs)** | LLM has no web access — URL field meaningless. Existing `evidence_url` DB column stays as vestigial (drop deferred). |
| Q-extra | **`evidence_url` column NOT dropped** | Pet-friendly minimal scope. Future cleanup task. V2 mapping always sets `evidence_url=None`. |
| Q9 | **17 new tests across 7 categories** | Comprehensive coverage of parser semantics (9 cases × 1 test each = explicit) + domain + mapper + migration sanity. Plus `−7` V1 deletes. Net +10. |

---

## Schema changes

### Domain `Prediction` (`src/prophet_checker/models/domain.py`)

**Нові enum:**
- `PredictionStrength` — values: `LOW`, `MEDIUM`, `HIGH`. Lives alongside `SourceType`, `PredictionStatus` (consistent location).

**Нові fields на `Prediction`:**
- `prediction_strength: PredictionStrength | None = None` — set-once на першій SUCCESSFUL verify
- `max_horizon: date | None = None` — set-once для premature без `target_date`
- `next_check_at: date | None = None` — mutually exclusive з `verified_at`; set on premature, clear on terminal
- `verify_attempts: int = 0` — counter SUCCESSFUL verify cycles only (NOT incremented on error)
- `last_verify_error: str | None = None` — text of last verifier error (`str(exception)`); cleared on next success
- `last_verify_error_at: datetime | None = None` — timestamp UTC of last error; cleared with `last_verify_error`

### DB model (`src/prophet_checker/models/db.py`)

`PredictionDB` отримує 6 нових columns + 1 index. Types per Postgres:
- `prediction_strength` → `VARCHAR(10)` nullable
- `max_horizon` → `DATE` nullable
- `next_check_at` → `DATE` nullable
- `verify_attempts` → `INTEGER NOT NULL DEFAULT 0`
- `last_verify_error` → `TEXT` nullable
- `last_verify_error_at` → `TIMESTAMP` nullable
- Index: `idx_predictions_eligible ON predictions (verified_at, next_check_at, max_horizon)`

`evidence_url` column — **залишається untouched** (vestigial, V2 mapping sets None).

### Mappers (`src/prophet_checker/storage/postgres.py`)

Both `domain_to_prediction_db` + `prediction_db_to_domain` round-trip 6 нових fields. `prediction_strength` маппиться як string ↔ enum.

### Alembic migration

New revision: `add_verification_metadata_v2`. `down_revision = "edb2e385f26b"` (Task 17 baseline).

`upgrade()`: 6 add_column calls + 1 create_index. `verify_attempts` має `server_default=0` (для backfill existing rows).

`downgrade()`: drop_index + 6 drop_column calls (LIFO order).

Approach: Alembic `--autogenerate` initial scaffold + manual edits для `server_default` and index.

---

## V2 Prompts

### `VERIFICATION_SYSTEM_V2`

Per spec [`2026-04-26-verification-trigger-policy-design.md`](2026-04-26-verification-trigger-policy-design.md), section "Verification prompt v2", з ОДИНОЮ зміною:

> **Original spec:** `"evidence": "concrete fact / URL or null"`
> **V2 final:** `"evidence": "concrete fact text or null. Do NOT include URLs (you have no web access)."`

Includes `{today}` placeholder — formatted via `get_verification_system_v2(today)`.

### `VERIFICATION_TEMPLATE_V2`

Per spec — substitutes `{claim}`, `{prediction_date}`, `{target_date}`, `{today}`, `{post_excerpt}`. Built via `build_verification_prompt_v2(...)`.

### Locations

Both prompts + builders live в `src/prophet_checker/llm/prompts.py`, alongside `EXTRACTION_SYSTEM` etc. Не окремий файл.

---

## V2 Parser

`parse_verification_response_v2(response: str) -> dict`

### Hard-reject (raise `ValueError`)

| Case | Trigger | Example exception message |
|------|---------|---------------------------|
| JSON parse error | Response не valid JSON | `JSONDecodeError` propagates (subclass of ValueError) |
| Missing required field | One of `{status, confidence, prediction_strength, reasoning}` absent | `missing required field: prediction_strength` |
| Invalid status enum | Не в `{confirmed, refuted, unresolved, premature}` | `invalid status: 'verified' (expected confirmed/refuted/unresolved/premature)` |
| Invalid strength enum | Не в `{low, medium, high}` | `invalid prediction_strength: 'strong' (expected low/medium/high)` |
| premature без retry_after | `status=premature` AND `retry_after is None` | `status=premature requires retry_after` |
| C/R без evidence | `status in {confirmed, refuted}` AND `evidence is None` | `status=confirmed requires evidence` |

**Empty string `""` для evidence:** treated as None (degenerate edge case). Same hard-reject rule for C/R.

### Soft-normalize (drop field, keep result)

| Case | Trigger | Action |
|------|---------|--------|
| Extraneous retry_after on terminal | `status in {confirmed, refuted, unresolved}` AND `retry_after is not None` | Drop `retry_after` (set to None у returned dict), log warning |
| Extraneous max_horizon on non-premature | `status != "premature"` AND `max_horizon is not None` | Drop `max_horizon` (set to None), log warning |

**Note:** parser НЕ перевіряє "max_horizon set ONLY when target_date IS NULL" rule — це orchestrator-level (Task 20) бо потребує access до `Prediction.target_date`.

### Output shape

Returns `dict` з canonical 7 keys: `status`, `confidence`, `prediction_strength`, `reasoning`, `evidence`, `retry_after`, `max_horizon`. Soft-normalized fields = None.

### Orchestrator catch behavior (preview for Task 20)

```
try:
    response = await llm.complete(...)
    parsed = parse_verification_response_v2(response)
except (json.JSONDecodeError, ValueError) as exc:
    pred.last_verify_error = str(exc)
    pred.last_verify_error_at = now()
    # NOT incrementing verify_attempts
    await repo.update(pred)
    continue   # next prediction
```

On success: clear `last_verify_error` + `last_verify_error_at`, increment `verify_attempts`.

---

## Deletes (V1 cleanup)

- `src/prophet_checker/analysis/verifier.py` — entire `PredictionVerifier` class
- `src/prophet_checker/analysis/__init__.py` — remove `PredictionVerifier` export
- `src/prophet_checker/llm/prompts.py` — delete `VERIFICATION_SYSTEM`, `VERIFICATION_TEMPLATE`, `build_verification_prompt`, `parse_verification_response`, `get_verification_system`
- `tests/test_analysis_verifier.py` — DELETE entire file (4 tests)
- `tests/test_llm_prompts.py` — delete V1 verification test cases (3 tests: `test_build_verification_prompt`, `test_parse_verification_response_valid`, `test_parse_verification_response_invalid_json`)

---

## Tests

### New test categories (17 tests total)

| Category | File | Count | What |
|----------|------|------:|------|
| A — Domain | `tests/test_models.py` | 2 | `PredictionStrength` enum + `Prediction` field defaults |
| B — Parser hard-reject | `tests/test_llm_prompts.py` | 6 | `pytest.raises(ValueError, match=...)` для кожного Q7 case |
| C — Parser soft-normalize | `tests/test_llm_prompts.py` | 3 | Drop extraneous + verify other fields preserved |
| D — Parser happy path | `tests/test_llm_prompts.py` | 2 | Valid terminal + valid premature |
| E — Prompt build | `tests/test_llm_prompts.py` | 1 | `build_verification_prompt_v2` parameter substitution |
| F — Mapper round-trip | `tests/test_storage_postgres.py` | 2 | domain ↔ DB з новими fields |
| G — Migration sanity | `tests/test_alembic.py` (NEW) | 1 | Migration script loads без import errors |

### Deletes (7 tests)

- `tests/test_analysis_verifier.py` — 4 tests deleted (file removed)
- `tests/test_llm_prompts.py` — 3 V1 verification tests removed

### Final count

123 (current) − 7 (deletes) + 17 (new) = **133**.

### Чого НЕ тестуємо в Task 19.5

- ❌ V2 verifier class behavior — не існує до Task 20
- ❌ `get_eligible_for_verification` repo method — Task 20
- ❌ `force_unresolved_past_horizon` — Task 20
- ❌ Orchestrator cycle — Task 20
- ❌ Real DB migration apply — manual smoke (Docker postgres + alembic upgrade head)
- ❌ Real LLM verifier eval — Task 19.7

---

## Manual smoke (post-implementation)

After Task 19.5 lands:

```bash
docker compose up -d
.venv/bin/alembic upgrade head
docker exec prophet_postgres psql -U prophet -d prophet_checker -c "\d predictions"
```

Expected: predictions table має 6 нових columns + index `idx_predictions_eligible`. `verify_attempts` shows `not null default 0`.

---

## Out of Scope (deferred)

- ❌ V2 verifier class (`PredictionVerifier.verify_v2`) — Task 20
- ❌ Repo methods (`get_eligible_for_verification`, `force_unresolved_past_horizon`) — Task 20
- ❌ HTTP endpoint `POST /verify/run` — Task 20
- ❌ Settings additions (`VERIFIER_PROVIDER`, `VERIFICATION_BATCH_SIZE`) — Task 20
- ❌ `evidence_url` column drop — future cleanup
- ❌ Verifier model evaluation — Task 19.7

---

## Cross-references

- **Authoritative spec:** [`2026-04-26-verification-trigger-policy-design.md`](2026-04-26-verification-trigger-policy-design.md)
- **Decomposition strategy:** [`2026-05-07-verifier-v2-decomposition.md`](2026-05-07-verifier-v2-decomposition.md)
- **Lifecycle reference:** [`2026-04-29-prediction-lifecycle.md`](2026-04-29-prediction-lifecycle.md)
- **Cycle reference:** [`2026-04-29-verification-cycle.md`](2026-04-29-verification-cycle.md)
- **Pattern source (migration):** [`../ingestion-to-aws/2026-05-07-docker-compose-design.md`](../ingestion-to-aws/2026-05-07-docker-compose-design.md) (baseline `edb2e385f26b`)
- **Pattern source (V1 → V2 cleanup):** [`../ingestion-to-aws/2026-05-01-llm-client-split-design.md`](../ingestion-to-aws/2026-05-01-llm-client-split-design.md) (clean delete pattern)
