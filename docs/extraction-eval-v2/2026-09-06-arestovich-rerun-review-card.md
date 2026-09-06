# Review card: rerun the Arestovich channel on DeepSeek

**Approved:** 2026-09-06 · **Design:** [2026-09-06-arestovich-rerun-design.md](2026-09-06-arestovich-rerun-design.md)

## Decisions

| # | Fork | Chosen | Why (1 line) | § |
|---|------|--------|--------------|---|
| 1 | Window the rerun covers | whole history: cursor → 1970-01-01 via `window.sh --all` | "the channel" means all; cost gap is small | §4 |
| 2 | Old 4177 predictions, before the run | full-row CSV dump to the laptop, then delete in one checked transaction | no provenance column; bot idle (0 queries / 7 d) | §4 |
| 3 | Second ingest cycle started while one runs | both timers off for the day (`timers.sh --uninstall` / `--install`); no app lock | user's call; one operator rule instead | §4 |
| 4 | The multi-hour cycle, where it is driven from | `ingest.sh --timeout 36000` under `caffeinate` | the cycle survives a dropped SSH | §4 |
| 5 | New predictions after ingest, who verifies them | one manual `verify.sh` run, no limit | complete the same day; same cost | §4 |
| 6 | A post DeepSeek cannot parse, mid-run | halt → re-run; same post again → cursor set to its `published_at` | existing behaviour; rare | §4 |
| 7 | The run, how it is recorded | `progress.md` before/after + `runbook/rerun-channel.md` | repeatable for other channels | §4 |
| 8 | `deploy/window.sh`, still untracked | committed with its test before the runbook cites it | a runbook must not cite an uncommitted file | §4 |

## Behavior contract

- After the run, every Arestovich prediction row was extracted by `deepseek-v4-flash`; no row from before the run remains.
- Podoliak and Portnikov rows and cursors are untouched, apart from the normal forward tick for new posts.
- Nothing in `src/` changes; the box keeps the same image.
- The old rows exist as a CSV on the laptop, restorable with `\copy` and without re-embedding.
- While the timers are off no scheduled tick runs; at the end both are installed and armed again.
- Gate: last Arestovich cycle `posts_failed = 0` with the cursor on the newest post; verify `failed = 0`; `verified = predictions`.
- A halt leaves the cursor on the failing post; nothing is lost; skipping a post is a manual act recorded in `progress.md`.

## Not in scope

- App lock, `limit` for `/ingest/run`, provenance column.
- Other channels' cursors (Podoliak at 2026-02-12); verifier, planner, generator, embeddings.
- `CLAUDE.md` line 69 (stale model sentence) — one-line fix, separate.
- Flip-flop PoC files on this branch.
