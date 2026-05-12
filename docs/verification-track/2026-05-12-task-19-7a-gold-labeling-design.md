# Task 19.7a — Gold Dataset Labeling Tool Design

**Status:** approved 2026-05-12
**Task:** 19.7a (verifier-v2 decomposition) — first sub-task of split 19.7
**Prerequisites:** ✅ Task 19.5 (V2 prompts/parser foundations)
**Next:** Task 19.7b (multi-model evaluation), then Task 20 (orchestrator)

**Authoritative spec (still valid):** [`../verifier-v2/2026-04-26-verification-trigger-policy-design.md`](../verifier-v2/2026-04-26-verification-trigger-policy-design.md)
**Decomposition strategy:** [`2026-05-07-verifier-v2-decomposition.md`](2026-05-07-verifier-v2-decomposition.md)

---

## TL;DR

Tool для creating gold-labeled verification dataset: Opus pre-fills 35 Arestovich predictions, user reviews each via interactive CLI, final dataset committed to git. Reusable artifact для Task 19.7b model evaluation і future re-evals (new model release, prompt iteration).

**Why Opus pre-fill + human review (vs. pure manual):** Opus generates plausible verdicts, human only judges accept/edit per-prediction. ~35 min total versus ~70 min pure manual.

**Why N=35:** All Flash Lite Arestovich predictions from existing `extraction_outputs.json` — no cherry-picking, no fresh extraction needed.

---

## Architectural Decisions (Q1–Q4)

| # | Decision | Rationale |
|---|----------|-----------|
| Q1 | **LLM-assisted (Opus pre-fill) + per-prediction human review** | Speed + still-gold quality. Pure manual = slower without quality gain. Self-consistency = biased. Historical-only = miss premature cases. |
| Q2 | **N = 35** (all Arestovich Flash Lite predictions) | Existing `extraction_outputs.json` has 35 Arestovich Flash Lite predictions from 17 non-empty posts (verified). No cherry-picking — use all. Pet-project tractable. |
| Q3 | **Source: existing `extraction_outputs.json`** (Task 13.5 artifact) | Real predictions Flash Lite produced (production extraction model). $0 fresh extraction cost. |
| Q4 | **Interactive CLI review** (not JSON edit) | Structured workflow, no JSON syntax errors, progress tracking, resumable. Worth ~50 extra lines of code. |

---

## Deliverables

### `scripts/verification_gold_prefill.py` (NEW)

One-shot script. Runs Opus through V2 verification prompt on all 35 candidate predictions, writes pre-filled JSON.

**Flow:**
1. Load `scripts/outputs/extraction_eval/extraction_outputs.json`
2. Extract Flash Lite Arestovich predictions (35 expected). Cross-reference з `scripts/data/sample_posts.json` для full post text (needed for `post_excerpt` parameter)
3. For each prediction:
   - Build V2 prompt via `build_verification_prompt_v2(claim, prediction_date, target_date, today=<run-date>, post_excerpt=<first 500 chars of post>)`
   - Build system message via `get_verification_system_v2(today)`
   - Call Opus via `LLMClient(provider="anthropic", model="claude-opus-4-6", api_key=settings.anthropic_api_key)`
   - Parse response via `parse_verification_response_v2`
   - If parser raises (hard-reject): log warning, store `null` pre-fill (user fills manually)
   - If parser succeeds: store parsed dict як `opus_prefill`
4. Write `scripts/outputs/verification_eval/gold_prefill.json` з structured entries
5. Print summary: N processed, N parse-failures, total cost estimate (~$0.18)

**Schema of `gold_prefill.json`:**

```
{
  "metadata": {
    "generated_at": "2026-05-12T...",
    "prefill_model": "anthropic/claude-opus-4-6",
    "today": "2026-05-12",
    "total_candidates": 35,
    "parse_failures": 0
  },
  "candidates": [
    {
      "id": "tg:O_Arestovich_official:215_0",
      "post_id": "O_Arestovich_official_215",
      "claim_text": "...",
      "prediction_date": "2021-...",
      "target_date": null,
      "post_excerpt": "...(first 500 chars)...",
      "opus_prefill": {
        "status": "premature",
        "confidence": 0.5,
        "prediction_strength": "medium",
        "reasoning": "...",
        "evidence": null,
        "retry_after": "2026-11-01",
        "max_horizon": "2028-01-01"
      }
    },
    ...
  ]
}
```

**`id` format:** `tg:<post_id>:<claim_index>` — unique per (post, claim) pair. Якщо post має 2 claims, indices 0 і 1.

**Cost:** ~$0.005 × 35 = ~$0.175. Opus 4.6 pricing ($5/1M input + $25/1M output).

**Concurrency:** sequential (Anthropic rate limits typically generous). No need для concurrency overrides — це one-shot batch.

**CLI args:**
- `--limit N` (optional, default = process all 35) — для quick testing з smaller sample
- `--output PATH` (optional, default = `scripts/outputs/verification_eval/gold_prefill.json`)

### `scripts/verification_gold_review.py` (NEW)

Interactive CLI tool. Reads pre-fill, presents each prediction one-by-one, lets user accept/edit/skip, saves progressively.

**Flow:**
1. Load `scripts/outputs/verification_eval/gold_prefill.json`
2. Load existing `scripts/data/verification_gold_labels.json` if exists (для resume)
3. For each candidate not yet reviewed:
   - Display prediction details:
     ```
     [N/35] id: tg:O_Arestovich_official:215_0
     Post:        O_Arestovich_official_215
     Claim:       "..."
     Pred date:   2021-...
     Target date: null
     Excerpt:     "..."
     
     Opus pre-fill:
       status:              premature
       prediction_strength: medium
       confidence:          0.5
       reasoning:           "Trump's term started recently — too early to assess."
       evidence:            null
       retry_after:         2026-11-01
       max_horizon:         2028-01-01
     
     Action: [a]ccept / [e]dit / [s]kip / [q]uit-save
     >
     ```
   - On `a` (accept): write entry to gold labels з Opus pre-fill values + reviewer_notes=""
   - On `e` (edit): prompt кожен field окремо. Enter — keep pre-fill, type new value — override.
     - For enum fields (status, prediction_strength): validate input, re-prompt on invalid
     - For dates: parse ISO format, validate
     - For text fields (reasoning, evidence, reviewer_notes): accept free text
   - On `s` (skip): exclude from gold (e.g., bad data, ambiguous prediction)
   - On `q`: save current progress, exit (resume on next run)
4. After each accept/edit: auto-save to `scripts/data/verification_gold_labels.json`
5. End-of-loop: print summary (N reviewed, N skipped)

**Schema of `verification_gold_labels.json`:**

```
{
  "metadata": {
    "started_at": "2026-05-12T...",
    "completed_at": "2026-05-12T..." | null,
    "total_entries": 30,
    "skipped": 5
  },
  "predictions": [
    {
      "id": "tg:O_Arestovich_official:215_0",
      "post_id": "O_Arestovich_official_215",
      "claim_text": "...",
      "prediction_date": "2021-...",
      "target_date": null,
      "post_excerpt": "...",
      "expected_status": "premature",
      "expected_strength": "medium",
      "expected_evidence": null,
      "expected_retry_after": "2026-11-01",
      "expected_max_horizon": "2028-01-01",
      "reviewer_notes": "User comment if any",
      "opus_prefill": {
        "status": "premature",
        ...
      }
    },
    ...
  ]
}
```

**`opus_prefill` preserved** для audit trail — можна compare пізніше "що Opus suggested vs що user corrected".

**CLI args:**
- `--prefill PATH` (default `scripts/outputs/verification_eval/gold_prefill.json`)
- `--output PATH` (default `scripts/data/verification_gold_labels.json`)

### `scripts/data/verification_gold_labels.json` (NEW, committed to git)

Final artifact. Committed for reproducibility (future re-evals reuse same gold). Sensitive data check: no API keys, no private user data — just public Telegram posts + verdict labels.

---

## File Layout

```
scripts/
  verification_gold_prefill.py     NEW (~120 lines)
  verification_gold_review.py      NEW (~200 lines з interactive CLI)
  data/
    verification_gold_labels.json  NEW — final gold (committed)
  outputs/
    verification_eval/             NEW directory (gitignored — intermediate artifacts)
      gold_prefill.json            generated by prefill script
```

`scripts/outputs/verification_eval/` додається до `.gitignore` якщо ще не gitignored через wildcard pattern.

---

## Out of Scope (deferred)

- ❌ **Multi-model evaluation** — Task 19.7b
- ❌ **Automated tests** — pure-script tools, manual smoke during use
- ❌ **Cost cap / budget enforcement** — pre-fill is $0.18, не worth complexity
- ❌ **Web UI for review** — pet-friendly CLI sufficient
- ❌ **Fresh extraction fallback** — existing 35 predictions > 30 minimum; fallback documented но не implemented (re-add якщо колись потрібно)
- ❌ **Multi-author gold dataset** — Arestovich only (consistent з extraction_outputs.json scope)

---

## Manual smoke procedure

After implementation lands:

```bash
# 1. Pre-fill (one-shot, ~3 min, costs ~$0.18)
.venv/bin/python scripts/verification_gold_prefill.py

# 2. Interactive review (~35 min, no cost)
.venv/bin/python scripts/verification_gold_review.py
# Review each entry, press a/e/s, type values for edits

# 3. Verify gold file exists
ls -la scripts/data/verification_gold_labels.json
# Expect ~30 entries (5 skipped is normal for ambiguous predictions)

# 4. Commit
git add scripts/data/verification_gold_labels.json
git commit -m "data: 30 verification gold labels (Task 19.7a)"
```

---

## Cross-references

- **Authoritative spec:** [`../verifier-v2/2026-04-26-verification-trigger-policy-design.md`](../verifier-v2/2026-04-26-verification-trigger-policy-design.md)
- **Decomposition strategy:** [`2026-05-07-verifier-v2-decomposition.md`](2026-05-07-verifier-v2-decomposition.md)
- **Task 19.5 (foundations):** [`2026-05-07-task-19-5-schema-prompts-design.md`](2026-05-07-task-19-5-schema-prompts-design.md)
- **Pattern source (extraction quality eval):** Task 13.5 (`scripts/extraction_quality_eval.py`)
