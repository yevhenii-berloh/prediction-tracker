# Verifier v2 — Implementation Decomposition

**Status:** Active strategy doc 2026-05-07
**Supersedes:** [`2026-04-29-verification-trigger-policy-plan.md`](2026-04-29-verification-trigger-policy-plan.md) (64KB monolith — predates Task 15-19 patterns)
**Authoritative spec (still valid):** [`2026-04-26-verification-trigger-policy-design.md`](2026-04-26-verification-trigger-policy-design.md)

---

## TL;DR

Original 2026-04-29 plan — один monolithic 9-task block з ~30 tests. Decomposed у **3 послідовні sub-tasks**:

| # | Task | Goal | Tests delta |
|---|------|------|-------------|
| 19.5 | **Schema + V2 prompts foundation** | Migration з 4 нових fields + `VERIFICATION_SYSTEM_V2` + `parse_verification_response_v2`, paralleled з V1 | +12 |
| 19.7 | **Verification model evaluation** | Multi-model eval script + golden labels + winner selection (mirrors Task 13.5 pattern) | 0 (eval script, no pytest) |
| 20 | **VerificationOrchestrator** | Production orchestrator + HTTP endpoint, using winner з 19.7 | +20 |

**Бажаний остаточний test count:** 123 → ~155 (vs original target 162; кілька schema-related tests integrated в Task 17 baseline migration).

---

## Чому decomposed

**Проти оригінального monolithic plan:**

1. **Insert eval between schema і orchestrator.** Master plan track has eval-before-production pattern (Task 13 detection eval → eventually used by orchestrator extraction). V2 design включає тільки 1-shot empirical validation на 10 claims з Opus. Insufficient — потрібно multi-model comparison як Task 13.5 для пошуку production winner.

2. **Predates Task 15-19 patterns.** Original 2026-04-29 plan написаний до:
   - Task 3: session-aware repo Protocol methods
   - Task 15: IngestionOrchestrator pattern (`session_factory + AsyncExitStack`)
   - Task 17: baseline migration `edb2e385f26b`
   - Task 19: integration smoke script pattern
   - Plan не використовує цих patterns — потребує переписки.

3. **Pet-friendly incremental ship.** Кожен sub-task ship'ається independently:
   - 19.5 lands → V1 verifier ще працює, V2 prompt готовий до evaluation
   - 19.7 lands → ми знаємо WHICH model use для production
   - 20 lands → orchestrator wired
   - Можна паузу між etapen, partial value делівернений.

4. **Reduced risk per task.** 9-task monolith з ~30 tests — high cognitive load. 3 focused tasks — легше review, легше debug якщо щось fail.

---

## Task 19.5: Schema + V2 prompts foundation

**Files (estimated):**
- New Alembic migration: `alembic/versions/<rev>_add_verification_metadata.py`
- Modify: `src/prophet_checker/models/domain.py` (PredictionStrength enum + 4 fields)
- Modify: `src/prophet_checker/models/db.py` (4 columns + index)
- Modify: `src/prophet_checker/storage/postgres.py` (mappers)
- Modify: `src/prophet_checker/llm/prompts.py` (V2 prompt + parser, paralleled з V1)
- Modify: `tests/test_models.py` (new field assertions)
- Modify: `tests/test_llm_prompts.py` (V2 parser tests)

**Що НЕ робить:**
- НЕ змінює `PredictionVerifier.verify()` сигнатуру (стара V1 ще працює as-is)
- НЕ додає orchestration logic
- НЕ додає `get_eligible_for_verification` / `force_unresolved_past_horizon` (це Task 20)

**Pattern reuse:**
- Migration через `alembic revision --autogenerate` (як Task 17 baseline)
- Domain field з `model_post_init` default (як `last_collected_at` у Task 1)

**Test target:** ~12 нових
- Domain: 2 (PredictionStrength enum + new field defaults)
- Mappers: 2 (round-trip з новими полями)
- Migration: 1 (sanity-check загрузки migration script)
- V2 prompt build: 2 (template substitution)
- V2 parser: 5 (mutual-exclusion validation cases)

---

## Task 19.7: Verification model evaluation

**Mirrors Task 13.5 pattern:** golden labels + multi-model eval + cost comparison.

**Files (estimated):**
- New: `scripts/data/verification_gold_labels.json` — manual annotation, ~30 claims
  - Format: `{"id": "...", "claim": "...", "expected_status": "confirmed|refuted|premature|unresolved", "expected_strength": "low|medium|high", "notes": "..."}`
- New: `scripts/verification_eval.py` — eval script
  - Multi-model loop через `LLMClient` с різними `(provider, model)` combos
  - Per-claim: invoke V2 prompt → parse → compare vs gold
  - Output: `outputs/verification_eval/results_<provider>_<model>.json`
- New: `outputs/verification_eval/model_comparison.md` — winner analysis + cost ledger
- (Possibly) New: `tests/test_verification_eval.py` — sanity tests for eval script (mocked LLM)

**Models to evaluate (suggested initial set):**
- Gemini Flash Lite Preview (extraction winner — does it transfer?)
- Gemini Pro Preview
- Claude Sonnet 4.5 (or current latest)
- Claude Opus 4.6 (likely best for factual reasoning)
- GPT-4o
- DeepSeek (cheap, multi-language)

**Metrics:**
- Per-model: confirmed/refuted/premature/unresolved accuracy vs gold
- Per-model: prediction_strength accuracy (low/medium/high) vs gold
- Per-claim: estimated cost
- Per-model: invalid mutual-exclusion rate (parser warnings)

**Output:** "Verifier production winner: <model X> з <metric>". Documented в `model_comparison.md`. Used by Task 20 для Settings defaults.

**Test target:** 0 pytest tests added. Eval script — runs ad-hoc, results checked-in JSON. Manual review.

---

## Task 20: VerificationOrchestrator

**Files (estimated):**
- Modify: `src/prophet_checker/analysis/verifier.py` — `PredictionVerifier.verify_v2(pred, today, post_excerpt)`
- Modify: `src/prophet_checker/storage/interfaces.py` — нові repo methods
- Modify: `src/prophet_checker/storage/postgres.py` — implementations
- New: `src/prophet_checker/ingestion/verification_orchestrator.py` — `VerificationOrchestrator.run_cycle(today)`
- Modify: `src/prophet_checker/factory.py` — wire VerificationOrchestrator
- Modify: `src/prophet_checker/app.py` — `POST /verify/run` endpoint
- New: `tests/test_analysis_verifier_v2.py` — verifier unit tests
- New: `tests/test_verification_orchestrator.py` — orchestrator unit tests
- New: `tests/test_verification_integration.py` — end-to-end з Fakes

**Pattern reuse from Task 15:**
- `session_factory` + `AsyncExitStack` for resource management
- Per-prediction atomic transaction (verify + update в session.begin())
- Halt-on-error semantics
- `CycleReport`-style telemetry (n_finalized, n_processed, n_terminal, n_premature, n_errors)

**HTTP integration (paralel до `/ingest/run`):**
- `POST /verify/run` — sync trigger, returns telemetry JSON
- Same defensive pattern: 503 if orchestrator missing, 500 on catastrophic exception, 200 з cycle report otherwise

**Settings additions:**
- `VERIFIER_PROVIDER` (e.g., `"anthropic"`)
- `VERIFIER_MODEL` (e.g., `"claude-sonnet-4-5"`)
- `VERIFICATION_BATCH_SIZE` (default 50)
- Plus default values determined by Task 19.7 eval winner

**Test target:** ~20 нових
- Verifier V2 unit: 6 (terminal vs premature paths, set-once invariants, failure symmetry)
- Repo methods: 4 (`get_eligible_for_verification` filters + `force_unresolved_past_horizon` bulk update)
- Orchestrator unit: 7 (housekeeping → fetch → verify each, halt-on-error, telemetry aggregation)
- Integration smoke: 3 (end-to-end з Fakes, run cycle on seeded predictions, halt recovery)

---

## Migration strategy між tasks

**После 19.5 lands:**
- Schema готова, V2 prompts готові, V1 ще active в коді
- DB має 4 нові columns (всі NULL для existing predictions)
- `PredictionVerifier.verify()` — V1 — ще працює as-is

**После 19.7 lands:**
- Знаємо production winner model
- Settings defaults готові до Task 20

**После 20 lands:**
- V2 orchestrator deployed
- V1 verifier deprecated (`# DEPRECATED — use verify_v2`)
- New predictions go through V2 cycle automatically
- Old predictions (extracted before V2 deploy) — на наступному verify cycle зустрінуть V2 verifier (їхні `prediction_strength=NULL` → set on first verify)

**Чого НЕ робимо у цьому 3-task chain:**
- ❌ Видалення V1 verifier code — якщо колись захочемо повернутись до simpler flow
- ❌ Migration of existing predictions in DB — let lazy migration через next-cycle verify
- ❌ News collector for evidence (Task 22+ — окрема ціль)
- ❌ Manual UI для re-verify помилково-terminal predictions

---

## Що відбувається з оригінальним plan'ом

`2026-04-29-verification-trigger-policy-plan.md` (64KB) — **superseded**. Не виконуємо as-is. Корисно як reference для:
- Test case ideas (особливо Group A pure functions)
- Edge case enumeration
- Migration field types

Але **не** як step-by-step execution guide. Поточні patterns (Task 15-19) — authority.

При executing 19.5/19.7/20 — кожен task має свій fresh plan written via writing-plans skill.

---

## Cross-references

- **Authoritative design (still valid):** [`2026-04-26-verification-trigger-policy-design.md`](2026-04-26-verification-trigger-policy-design.md)
- **Superseded plan:** [`2026-04-29-verification-trigger-policy-plan.md`](2026-04-29-verification-trigger-policy-plan.md)
- **Lifecycle reference:** [`2026-04-29-prediction-lifecycle.md`](2026-04-29-prediction-lifecycle.md)
- **Cycle reference:** [`2026-04-29-verification-cycle.md`](2026-04-29-verification-cycle.md)
- **Single call reference:** [`2026-04-29-verifier-v2-call.md`](2026-04-29-verifier-v2-call.md)
- **Pattern source (orchestrator):** [`../ingestion-to-aws/2026-05-01-ingestion-orchestrator-design.md`](../ingestion-to-aws/2026-05-01-ingestion-orchestrator-design.md)
- **Pattern source (eval):** [`../architecture/2026-04-26-flow-4-extraction-quality-eval.md`](../architecture/2026-04-26-flow-4-extraction-quality-eval.md)
