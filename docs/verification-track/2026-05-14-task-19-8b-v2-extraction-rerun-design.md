# Task 19.8b — V2 Extraction Re-run + Quality Re-eval + Fresh Gold Labeling

**Status:** draft 2026-05-14
**Task:** 19.8b (operational — V2 extraction run + Task 13.5-style quality eval + new gold)
**Prerequisites:** ✅ Task 19.5 (V2 verifier foundations), ✅ Task 19.7a (gold v1, archive baseline), ⏳ Task 19.8a (extraction context schema/prompt — must be landed first)
**Downstream:** Task 19.7b verification eval (отримує fresh gold з V2-extracted context)

---

## TL;DR

Прогнати оновлений V2 extraction prompt (з `context` field) через Gemini Flash Lite на тих самих 17 Arestovich постах, що дали V1 baseline. Перевірити що V2 prompt не погіршує extraction quality (Task 13.5 LLM-as-judge methodology, абсолютна оцінка кожного claim). Потім **повністю re-розмітити** новий V2 output як свіжий gold dataset для 19.7b verification eval — старий gold уходить в `_legacy/` як baseline для порівняння distributions.

**Why fresh re-labeling vs. backfill matching:** V2 extraction може видати трохи інші claims (різний phrasing, ±1-2 claims per post). Спроби автоматично матчити (Jaccard / Opus judge) додають complexity і кост. При n=35 простіше переразмітити з нуля — тим більше що V2 уже доставляє context inline, тож labeling швидший (контекст не треба окремо шукати).

---

## Goals (явно)

1. **Validate V2 prompt:** Чи додавання поля `context` не погіршило baseline extraction quality (verdict distribution, missed_predictions count)?
2. **Validate V2 contexts:** Чи модель видає **valid verbatim quotes** (substring check pass rate)?
3. **Produce fresh gold:** Новий `verification_gold_labels.json` з полем `context` для всіх entries — input для Task 19.7b.

---

## Architectural decisions

| # | Рішення | Обґрунтування |
|---|---|---|
| Q1 | **Run scope = 17 Arestovich постів** (джерело V1 gold) | Direct V1 vs V2 порівняння на тому самому set. Cost ~$0.02. |
| Q2 | **Тільки Gemini Flash Lite** (single model) | Task 13.5 виявила Gemini Flash Lite production winner для extraction. Тут перевіряємо чи V2 prompt не зламав цю модель. |
| Q3 | **Substring validation у post-processing** | Reuse `validate_context_in_post` з 19.8a. Drop hallucinations. |
| Q4 | **Quality re-eval — Task 13.5 methodology без змін** | Opus judge оцінює кожен V2 claim абсолютно (6-категорійна шкала), output порівнюється з V1 baseline. |
| Q5 | **Fresh re-labeling, no matching** | При n≈35 простіше переразмітити з нуля, ніж матчити V1 ↔ V2. |
| Q6 | **Old gold → `_legacy/`** | Зберігаємо для baseline distribution comparison. |
| Q7 | **Re-labeling inline chat** (як 19.7a) | Pattern перевірений. Pre-fill з context з V2 → user a/e/s. |

---

## Pipeline

### Stage 1: V2 extraction (~3 min, $0.02)

```python
# scripts/v2_extraction_run.py
# Reuse _default_extractor_factory з evaluate_detection.py
# Reuse validate_context_in_post з prompts.py

for post in arestovich_posts_17:
    extractor = _default_extractor_factory("gemini/gemini-3.1-flash-lite-preview")
    claims = await extractor.extract(post.text, post.published_date, person_name="Арестович")
    for claim in claims:
        if validate_context_in_post(claim["context"], post.text):
            valid_claims.append(claim)
        else:
            logger.warning(f"Drop hallucinated context: {claim['claim_text'][:60]}")
            stats["hallucinations"] += 1

save → scripts/outputs/verification_eval/v2_extraction_outputs.json
```

**Input:** `scripts/data/sample_posts.json` filtered до 17 непорожніх Arestovich постів (post_ids from V1 baseline).

**Output structure:**
```json
{
  "metadata": {
    "model": "gemini/gemini-3.1-flash-lite-preview",
    "prompt_version": "v2",
    "run_at": "2026-05-14T...",
    "posts_input": 17,
    "claims_extracted_raw": N,
    "claims_hallucinated_drop": K,
    "claims_kept": N-K
  },
  "extractions": [
    {
      "post_id": "O_Arestovich_official_1395",
      "post_published_at": "2021-10-06",
      "post_text": "...",  // full post for downstream judge
      "claims": [
        {
          "claim_text": "...",
          "context": "...verbatim quote...",
          "prediction_date": "2021-10-06",
          "target_date": null,
          "topic": "міжнародні відносини",
          "context_validated": true
        }
      ]
    }
  ]
}
```

### Stage 2: Quality re-eval з Opus judge (~5 min, $0.50)

Reuse Task 13.5 judge prompts + aggregation (`scripts/extraction_judge_prompts.py`, `scripts/extraction_quality_eval.py`).

```python
# scripts/v2_quality_eval.py — або extend extraction_quality_eval.py з --v2 mode
for post_extraction in v2_extraction_outputs:
    prompt = build_judge_prompt(post_extraction["post_text"], post_extraction["claims"])
    response = await opus.completion(prompt)
    judgement = parse_judge_response(response)
    judgements[post_id] = judgement

# Aggregate (reuse aggregate_metrics function):
metrics = aggregate_metrics(judgements, gold_labels_detection)

save → scripts/outputs/verification_eval/v2_judgements.json
save → scripts/outputs/verification_eval/v2_quality_eval_report.md
```

**Comparison:** new metrics vs `scripts/outputs/extraction_eval/extraction_eval_report.json` (V1 baseline for Gemini Flash Lite).

**Key questions to answer:**
- Verdict distribution: чи зросла частка `hallucination` через те, що модель тепер ще й context фабрикує?
- `missed_predictions` count: чи V2 prompt відволікає модель на context і вона пропускає predictions?
- `quality_score` (ordinal mean): зріс / впав / залишився той самий?

**Decision rule для V2 prompt acceptance:**
- ✅ Accept V2: ordinal_mean within ±0.2 від V1 baseline AND hallucination_rate ≤ V1 + 5pp
- ⚠️ Tune V2: значне погіршення → переглянути prompt wording (можливо "context" instruction конфліктує з extraction focus)
- ❌ Reject V2: catastrophic regression → revert до V1 schema + context як окремий enrichment stage (alternative design)

### Stage 3: Fresh re-labeling (inline chat, ~1.5h, $0)

**Input:** `v2_extraction_outputs.json` (clean claims з contexts)

**Workflow:** як 19.7a, але context **уже видно** на старті:

```
[N/M] id: tg:O_Arestovich_official_1395:0
Post:        O_Arestovich_official_1395
Claim:       "..."
Pred date:   2021-10-06
Target date: null
Topic:       міжнародні відносини
Context:     "...verbatim quote з V2 extraction..."    ← вже є

Pre-fill (Claude):
  status:              unresolved
  confidence:          0.55
  prediction_strength: low
  prediction_value:    medium
  reasoning:           "..."
  evidence:            null
  retry_after:         null
  max_horizon:         null

Action: [a]ccept / [e]dit / [s]kip / [q]uit-save
```

Re-labeling швидший за 19.7a бо context дано — не треба окремо запитувати "дай контекст".

**Output:**
- `scripts/data/verification_gold_labels.json` (NEW, V2 schema з context)
- `scripts/data/_legacy/verification_gold_labels_v1.json` (старий, archive)

### Stage 4: Comparison + commit

```bash
# Move old to legacy
mkdir -p scripts/data/_legacy
git mv scripts/data/verification_gold_labels.json scripts/data/_legacy/verification_gold_labels_v1.json

# New file written by Stage 3
# Compare distributions (status/strength/value) — sanity check, not blocker
git add scripts/data/verification_gold_labels.json
git commit -m "data: fresh gold (V2 extraction context) for verification eval"
```

---

## Output artifacts

| Артефакт | Що містить |
|---|---|
| `scripts/outputs/verification_eval/v2_extraction_outputs.json` | Raw V2 extraction для 17 постів, з context + validation flags |
| `scripts/outputs/verification_eval/v2_judgements.json` | Opus judge verdicts per-claim (Task 13.5 format) |
| `scripts/outputs/verification_eval/v2_quality_eval_report.md` | Markdown comparison V1 vs V2 (verdict distribution, ordinal mean, decision) |
| `scripts/data/verification_gold_labels.json` | **NEW** fresh gold з context для 19.7b |
| `scripts/data/_legacy/verification_gold_labels_v1.json` | Archived V1 gold (без context) |

`scripts/outputs/verification_eval/` уже gitignored (per 19.7a spec).

---

## Scripts to create

| File | Purpose | Reuse |
|---|---|---|
| `scripts/v2_extraction_run.py` | Stage 1 runner | `_default_extractor_factory`, `validate_context_in_post` |
| `scripts/v2_quality_eval.py` | Stage 2 runner (judge + aggregate) | `build_judge_prompt`, `parse_judge_response`, `aggregate_metrics` з extraction_quality_eval.py |

**Можлива оптимізація:** extend `extraction_quality_eval.py` з прапором `--v2` що змінює input source і output paths. Уникнути дублювання.

---

## Tests

Pet project — **минімальні**:
- Reuse pure aggregation tests з extraction_quality_eval (вже існують)
- Smoke test: run з `--limit 2` на 2 постах перед full run
- No new pytest tests створюємо

---

## Cost & time estimate

| Stage | Cost | Time |
|---|---|---|
| Stage 1: V2 extraction | $0.02 | ~3 min |
| Stage 2: Opus judge | $0.50 | ~5 min |
| Stage 3: Re-labeling inline | $0 | ~1-1.5h |
| **Total** | **~$0.52** | **~1.5-2h** |

---

## Out of scope

- ❌ Multi-model extraction comparison (тільки Gemini Flash Lite — production candidate)
- ❌ Verification model eval (Task 19.7b — наступний step)
- ❌ Production extractor wiring (Task 20)
- ❌ Matching V1↔V2 claims, Opus matcher для backfill — replaced by fresh re-labeling
- ❌ Розширення gold > 35 entries (sample_posts_100.json) — out of scope
- ❌ Re-evaluating intermediate models (Sonnet/GPT) на V2 extraction — Gemini Flash Lite only

---

## Risks & mitigations

| Risk | Probability | Mitigation |
|---|---|---|
| V2 extraction видає катастрофічно низьку якість (>20% hallucinations) | Low | Stage 2 judge виявить. Decision rule: revert до окремого enrichment stage. |
| Substring validation drops > 30% claims | Medium | Якщо багато drops через whitespace edge-cases — посилити normalize (NFKC unicode, punctuation). |
| V2 produces too few claims (model focuses on context, drops claims) | Medium | Stage 2 `missed_predictions` count покаже. Якщо >5 missed — потрібно tuning prompt. |
| Re-labeling burnout (~1.5h tedious work) | High (UX) | Pre-fill з V2 context робить процес швидшим. Break-resume pattern як 19.7a (auto-save кожні N entries). |
| New gold distribution drifts далеко від V1 (наприклад 50% premature замість 34%) | Low | Stage 4 sanity check. Якщо суттєвий drift — переглянути чи V1 був biased. Не блокер. |

---

## Cross-references

- **V2 verification spec:** [`../verifier-v2/2026-04-26-verification-trigger-policy-design.md`](../verifier-v2/2026-04-26-verification-trigger-policy-design.md)
- **Task 19.7a gold v1:** [`2026-05-12-task-19-7a-gold-labeling-design.md`](2026-05-12-task-19-7a-gold-labeling-design.md)
- **Task 19.8a schema + prompt:** [`2026-05-14-task-19-8a-extraction-context-schema-design.md`](2026-05-14-task-19-8a-extraction-context-schema-design.md)
- **Task 13.5 baseline:** `docs/extraction-quality-eval/2026-04-21-extraction-quality-eval-design.md`
- **Production extractor pipeline:** `scripts/extraction_quality_eval.py`, `scripts/evaluate_detection.py`
