# Review card: switch prod extractor to DeepSeek

**Approved:** 2026-08-21 · **Design:** [2026-08-21-deepseek-switch-design.md](2026-08-21-deepseek-switch-design.md)

## Decisions

| # | Fork | Chosen | Why | § |
|---|------|--------|-----|---|
| 1 | What lands on main first | merge `feat/extraction-eval-v2` whole | its 7 src fixes are what survive a model swap | §4 |
| 2 | Which roles switch | extractor only | the only role eval v2 measured | §7 |
| 3 | Model + where it is declared | `deepseek/deepseek-v4-flash` as code default **and** in S3 env | a silent default is how prod drifted to gpt-4o-mini | §4 |
| 4 | Empty `LLM_API_KEY` | raise in `Settings`, app does not start | today it falls through to another provider | §4 |
| 5 | Temperature | 0.0 (was 0.1) | eval measured DeepSeek at 0.0 | §4 |
| 6 | Determinism 0.20 | accepted, no mitigation | user's call, 2026-08-21 | §5 |
| 7 | First prod run | full backlog since 17 Aug, no `limit` | ≈$0.001/post, no new endpoint code | §5 |
| 8 | Box after verify | stays running | user's call, 2026-08-21 | §4 |
| 9 | Verification | local smoke on 10 real posts → pre-check on the live box → one prod cycle | API shape fails cheaply off AWS; the pre-check proves what prod actually ran | §3, §4 |

## Behavior contract

- Ingestion extracts with `deepseek-v4-flash` at temperature 0.0.
- App refuses to start when `LLM_API_KEY` is empty. Today it silently uses `OPENAI_API_KEY`.
- A response the parser cannot read marks the post `failed`, the cursor stays, the channel
  stops on that post.
- Before `deploy.sh`, the still-old container prints its effective `llm_provider` /
  `llm_model`. If it is not `openai gpt-4o-mini`, the rollout stops and the design is revised.
- Gate: one prod cycle with `posts_failed = 0` and `predictions_extracted > 0`.
- Rollback is three `secrets.sh set` calls plus a redeploy. No code change, no rebuild.
- Verifier, planner, answer generator and embeddings behave exactly as today.

## Not in scope

- Verifier / planner / answer generator (stay on Gemini Flash Lite), embeddings (stay on OpenAI).
- Extraction prompt, eval slate, determinism re-measurement, `limit` for `/ingest/run`.
