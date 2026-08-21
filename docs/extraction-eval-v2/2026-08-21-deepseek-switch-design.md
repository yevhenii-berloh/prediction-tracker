# Switching the production extractor to DeepSeek — design

**Date:** 2026-08-21
**Track:** extraction-eval-v2 (closes the open decision from the 2026-08-15 run)
**Status:** design approved via the G1 card

---

## 1. Why this exists

Production extraction runs on a model nobody measured.

The `.env` in the S3 secrets bucket sets neither `LLM_PROVIDER`, nor `LLM_MODEL`, nor
`LLM_API_KEY` (checked with `deploy/secrets.sh list` on 2026-08-21 — 13 keys in the file,
none of the three). So `Settings` falls back to the code defaults: `openai` / `gpt-4o-mini`.
The empty `llm_api_key` travels on into litellm, which for provider `openai` quietly picks
up `OPENAI_API_KEY` from the environment. That key is present in the prod `.env`, so
everything works — and that is exactly why the drift went unnoticed.

Eval v2 started from a different picture. The comment in
`scripts/extraction/eval_v2/extraction_eval.py` says "prod runs the `-preview` id", and the
baseline is `gemini-3.1-flash-lite`. For the verifier, the planner and the answer generator
that is true — they are hardcoded in `factory.py`. For the extractor it is not. The run
compared DeepSeek against something production does not use.

Numbers from that run (130 posts, judge `claude-opus-5`, health clean):

| model | halluc | precision | coverage | over-extr | determinism | $/post |
|---|---:|---:|---:|---:|---:|---:|
| `gemini-3.1-flash-lite` (eval baseline) | 0.010 | 0.580 | 0.666 | 0.410 | 1.00 | 0.001 |
| `deepseek-v4-flash` | 0.000 | 0.706 | 0.738 | 0.294 | 0.20 | 0.001 |
| `gpt-4o-mini` (**actual production**) | not measured | — | — | — | — | — |

DeepSeek beats the baseline on every quality axis and is the cheapest of the four.
Production runs a model that is not in the table at all.

This section reads configuration, not a running process — the box was stopped when it was
written. Step 6 of the rollout prints the effective setting on the live box before anything
is changed, and the switch does not proceed if that output disagrees.

## 2. What it gives

Production extraction runs on the model with the best measured numbers.

The second, less obvious gain: the model that goes to production becomes visible in the
repository. The code default matches what is written into S3, and an empty key breaks
startup instead of quietly handing requests to a different provider. After this change the
drift described in §1 cannot repeat.

## 3. What counts as success

**Production gate:** one ingestion cycle after the switch returns `posts_failed = 0` on
every channel and `predictions_extracted > 0`.

**Local gate, before touching production:** 10 real posts run through DeepSeek on the local
machine — zero failures, and the extracted claims look sane on inspection.

Not a gate: extraction quality. Eval v2 measured it on 130 posts with a judge. A single
production cycle would not show it anyway.

## 4. How it works inside

### Rollout order

The merge comes first, and this is not a formality. Seven production commits from the eval
session live only on `feat/extraction-eval-v2`: a bare JSON array in the response is now
accepted, an unparsable response yields `failed` instead of an empty result, and the cursor
does not advance on a failure. Those are precisely the three rules that absorb the shock of
a model change. The box pulls `main`, so without the merge the switch would ship on code
that swallows an unfamiliar response and loses the post.

1. Merge `feat/extraction-eval-v2` into `main`, push to the public repo.
2. Code changes (below) as a separate commit on `main`, push.
3. Local smoke on 10 real posts.
4. `deploy/secrets.sh set` — three keys into S3.
5. Start the box (`runbook/stop-env.md`).
6. **Pre-check on the live box, before `deploy.sh`.** The container still runs the old image
   and the old `.env`, so this reads the configuration production has been using:

   ```bash
   sudo docker compose -f docker-compose.yml exec app python -c \
     "from prophet_checker.config import get_settings; s=get_settings(); print(s.llm_provider, s.llm_model)"
   ```

   Expected: `openai gpt-4o-mini`. Anything else means §1 is built on a wrong reading —
   stop, record what it actually printed, and revise the design before deploying.
7. `deploy/deploy.sh` (`--force-recreate` re-reads `env_file`).
8. `deploy/ingest.sh` — one cycle, read the `CycleReport`.
9. The box stays up.

### Three code changes

**Default in code.** `Settings.llm_provider` → `deepseek`, `Settings.llm_model` →
`deepseek-v4-flash`. The default is what ships when the environment says nothing, so it has
to name the same model the environment names. Otherwise §1 repeats under a different name.

**Fail-fast on an empty key.** A validator on `Settings` raises `ValueError` when
`llm_api_key` is empty. Failure belongs at startup — `deploy.sh` has a health loop that will
catch it — not on the first post, and never as a silent fallback to another provider.

The price of this decision: about 18 scripts construct `Settings()` without needing the
extractor at all (retrieval evals, embed corpus, verification cycle). After the change they
all require `LLM_API_KEY` in the local `.env`. That is one line in `.env` and one in
`.env.example`. The test suite already passes `llm_api_key` explicitly, so it will not
notice.

**Temperature 0.0.** `LLMClient` defaults to 0.1, while the eval measured at 0.0.
`build_orchestrator` passes `temperature=0.0` explicitly so production matches what was
measured.

### Rollback

`secrets.sh set LLM_PROVIDER openai`, `LLM_MODEL gpt-4o-mini`, `LLM_API_KEY <OpenAI key>`,
then `deploy.sh` again. Two minutes, no rebuild. The code default stays DeepSeek — the
rollback lives in the environment, exactly like the switch itself.

## 5. What can go wrong

| Risk | What happens | Rule that handles it |
|---|---|---|
| DeepSeek returns an envelope the parser does not know | post → `failed`, cursor stays, the channel stops on it | already fixed on the branch — that is why the merge is step 1 |
| Empty or wrong `LLM_API_KEY` | the app does not start, the health loop in `deploy.sh` fails | the `Settings` validator |
| DeepSeek unreachable or rate-limited | `num_retries=3` in litellm, then the post → `failed` | existing behaviour, nothing added |
| Non-determinism 0.20 | the same post re-run yields a different set of claims; future prompt edits get harder to measure | **accepted by the user on 2026-08-21, no mitigation** |
| The backlog since 17 Aug goes in one cycle | a few hundred posts at once on a new model, ≈$0.001/post | accepted; no `limit` added to `/ingest/run` |
| The DeepSeek key sits in S3 twice (`DEEPSEEK_API_KEY` and `LLM_API_KEY`) | rotation means two writes | accepted; explicitness beats single-copy |
| `CycleReport` has no cost field | the cycle's cost is visible only on the DeepSeek dashboard | out of scope |

## 6. What covers it

**Unit tests:** the validator raises on an empty `llm_api_key`; `Settings` defaults are
`deepseek` / `deepseek-v4-flash`; `build_orchestrator` returns an `LLMClient` with
`temperature=0.0`.

**Local smoke:** 10 real posts, the real DeepSeek API, zero failures.

**Production gate:** `posts_failed = 0` in one ingestion cycle.

## 7. Scope

**Not included:**

- The verifier, the planner and the answer generator stay on Gemini Flash Lite (hardcoded in
  `factory.py`).
- Embeddings stay on OpenAI — DeepSeek has no embedding API.
- The extraction prompt does not change.
- The eval slate does not change; `gemini-3.1-flash-lite` remains a participant.
- The `determinism` metric is not re-measured (its blind spot is described in `progress.md`,
  2026-08-15).
- No `limit` is added to `/ingest/run`.
