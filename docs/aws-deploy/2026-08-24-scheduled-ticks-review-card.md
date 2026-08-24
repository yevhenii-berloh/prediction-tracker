# Review card: scheduled ingest/verify ticks on the AWS box

**Date:** 2026-08-24 · **Status:** approved (2026-08-24) · **Gate:** G1
**Design:** [`2026-08-24-scheduled-ticks-design.md`](2026-08-24-scheduled-ticks-design.md) — `§` points at the section behind each row.

## Decisions
| # | Fork | Chosen | Why (1 line) | § |
|---|------|--------|--------------|---|
| 1 | Tick scope | two independent timers: ingest, verify | separate cadence and cost knobs | §2 |
| 2 | Where the schedule lives | systemd timers on the EC2 box | no compute-stack update → no AMI-repin risk | §3 |
| 3 | Cadence + cost cap | ingest 05:00 UTC, verify 06:00 UTC `?limit=50` | bounded daily LLM spend | §4 |
| 4 | Overlap | one shared `flock` in the on-box tick script | serialises both timers, zero `src/` change | §5 |
| 5 | Visibility | journald + new `deploy/timers.sh` over SSH | pull-based, no new secrets | §6 |
| 6 | How units reach the box | over SSH (`timers.sh --install`), not `UserData` | a CFN update on `prophet-compute` kills the box | §3 |
| 7 | Missed ticks | `Persistent=false` — no catch-up on boot | un-pausing the env must not burn LLM money | §5 |

**Irreversible here:** installing the units is reversible (`--uninstall`); what the ticks do is not — every tick writes to the prod DB and spends LLM money.

## Behavior contract
- Each tick POSTs to the box's own `localhost:8000` (`/ingest/run`, `/verify/run?limit=50`).
- Two ticks never run at once: lock held → log `skipped: previous tick still running`, exit 0.
- Every tick writes one summary line to journald: unit, HTTP code, counts from the report.
- Non-200 → the unit fails, and the failure stays visible in `timers.sh` output.
- Env paused → no ticks at all; on un-pause timers resume, missed ticks are not replayed.
- `./deploy/timers.sh` prints next-run/last-run + last results; `--install`, `--uninstall`, `--run`, `--dry-run`.
- `./deploy/ingest.sh` / `verify.sh` keep working unchanged; a manual run bypasses the lock.

## Not in scope
- App-side 409 concurrency lock — `src/` is not touched at all.
- EventBridge / SSM / any CloudFormation change.
- Telegram alerts on tick results.
- Verifier recheck-loop (`next_check_at`), auto start/stop of the env, GitHub Actions CI.

## Zones & evidence
- All-green diff (shell, unit files, docs, tests) → one commit, no red-zone review needed.
- TDD per the existing `tests/test_deploy_*.py` pattern: hermetic pytest, fake `aws`/`ssh`/`curl`/`flock`.
- Live proof: install on the box, `systemd-analyze verify`, one forced run with `limit=5`, `timers.sh` output.

**Known limitation:** a rebuilt `prophet-compute` box loses the timers until `--install` is re-run (§9, follow-up 1).
