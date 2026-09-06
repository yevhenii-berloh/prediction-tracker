# Rerunning the Arestovich channel on DeepSeek — design

**Date:** 2026-09-06
**Track:** extraction-eval-v2 (follow-up of the 2026-08-21 extractor switch)
**Status:** design approved via the G1 card on 2026-09-06
**Forks decided by the user on 2026-09-06:** 1a whole history · 2a dump, then delete ·
3b timers off by hand, no app lock · 4a `ingest.sh` from the laptop · 5a one manual
verify run · 6a halt-and-resume on a bad post · 7a progress entry + runbook

---

## 1. Why this exists

The production extractor moved to `deepseek-v4-flash` on 2026-08-21. The corpus did not
move with it. Of the 4177 Arestovich predictions in prod today, only a few dozen were
extracted after the switch. The rest come from `gpt-4o-mini` — the code default nobody
chose and no eval measured (see the switch design, §1). Every consumer reads that corpus:
the bot's answers, the hit-rate statistics in `psql.sh --author`, and the flip-flop PoC,
which starts from the same posts.

The pipeline only moves forward. The channel cursor (`person_sources.last_collected_at`)
points at the newest processed post, so old posts are never read again. Without a
deliberate rerun the two models stay mixed in one table forever, and there is no column to
tell them apart.

Live state on 2026-09-06 (read-only checks on the box):

| Fact | Evidence |
|---|---|
| Extractor is DeepSeek | app container env `LLM_PROVIDER=deepseek`, `LLM_MODEL=deepseek-v4-flash`; S3 `.env` has the three LLM keys since 2026-08-21 |
| Arestovich corpus | 2264 docs, 4177 predictions, all verified; cursor 2026-09-04 16:50 UTC |
| Other channels | Podoliak 114 docs / 201 predictions (cursor 2026-02-12); Portnikov 4 / 5 |
| Timers | ingest 05:00 UTC (curl limit 3600 s); verify 06:00 UTC with `?limit=500` |
| Last full-history ingest | 2026-07-15, 6.6 h for this channel, one cycle |
| Bot traffic | 0 queries in 7 days (`psql.sh --queries`) |

## 2. What it gives

One channel, one model, one measured extractor behind every row.

A before/after on the same ~6k posts: how many predictions each model finds and how the
verdicts split. That number does not exist yet — eval v2 compared models on 130 posts
with a judge, not on the full channel.

A runbook. "Rerun channel X on the current extractor" will come back: for the other two
channels, and after the next model change.

## 3. What counts as success

Gate, all four required:

1. The Arestovich channel is read to the end: the last cycle reports `posts_failed = 0`
   and its cursor stands on the newest post in the channel. Total `predictions_extracted`
   across the cycles is greater than zero.
2. One manual verification run reports `failed = 0`; `psql.sh --stats` shows
   `verified = predictions` for Arestovich.
3. `timers.sh` shows both timers enabled again, with the next elapse.
4. The before/after snapshot is written to `progress.md`.

Not a gate: extraction quality. Eval v2 measured it on 130 posts with a judge; a
production run cannot.

## 4. How it works inside

### The pipeline already does this — nothing changes in `src/`

- **Cursor = frontier.** `TelegramSource.collect` reads the channel from the cursor. A
  cursor with year ≤ 1970 means "from the beginning" (`sources/telegram.py:36`).
  `deploy/window.sh --all` writes exactly that value.
- **Per-post resume.** The cursor advances per post, in the same transaction as the post's
  predictions (`ingestion/orchestrator.py`, `_save_post`). A cycle that stops anywhere —
  a halt on a bad post, a box restart — continues from the last processed post on the next
  `POST /ingest/run`.
- **Documents are upserted, predictions are inserted.** `raw_documents.id` is
  `tg:<channel>:<msg id>` and is saved with `session.merge`, so reading a post again
  updates the same row. Predictions get fresh UUIDs on every extraction. This is why the
  old prediction rows must be deleted, and why nothing else must be.
- **Other channels ride along.** `run_cycle` walks every enabled source. Podoliak and
  Portnikov continue from their own cursors — a handful of posts, as on any tick.

### The old rows go first (fork 2a)

The table has no provenance column. After the run, old and new rows for the same post
could be told apart only by an id list saved beforehand. Deleting first keeps one
invariant for the whole operation: every Arestovich prediction in the table came from
DeepSeek. The price is a corpus gap for the bot of about a day; the query log shows zero
queries in the last 7 days.

Insurance: a CSV dump of the full rows (`select *`, so `embedding` is included and a
restore needs no re-embedding) on the laptop, outside git. Restore path, if ever needed:
`\copy predictions from <file> csv header` — the columns match because the dump is the
whole row.

The delete runs in one transaction that also checks the result before committing: the
person's count must be 0 and the total of the other authors must be unchanged. The
transaction rolls back otherwise.

### No app lock (fork 3b), and the one rule it leaves behind

Uvicorn keeps running a request handler after the client disconnects. Verified locally on
2026-09-06: a 4 s handler finished after a 1 s curl timeout. So a cycle outlives any curl
timeout, and a second `POST /ingest/run` would walk the same posts and insert duplicates.

The scheduled tick is the only automatic caller. For the day of the run both timers are
removed with `timers.sh --uninstall` and restored with `timers.sh --install` afterwards.
The verify timer goes too, so the manual verification run cannot overlap it. The manual
path stays unguarded, which leaves one operator rule:

> Never start a second `ingest.sh` until the app log shows the
> `ingestion <source id> done:` line for the Arestovich source.

### Rollout order

0. **Preflight, read-only.** Env `UP` (`status.sh`); the box runs
   `deepseek / deepseek-v4-flash` (`connect.sh -- printenv LLM_PROVIDER LLM_MODEL`);
   timer state (`timers.sh`); the "before" snapshot — `psql.sh --stats` and
   `psql.sh --author Арестович` — saved to files next to the dump.
1. **Timers off.** `timers.sh --uninstall`.
2. **Dump.** `\copy` of every Arestovich prediction row to the laptop. Row count in the
   file equals the count in the "before" snapshot.
3. **Delete.** One checked transaction, as above. Expected `DELETE 4177`.
4. **Cursor.** `window.sh --author @O_Arestovich_official --all`. Expected `UPDATE 1`
   and `cursor_after = 1970-01-01`.
5. **Ingest.** `ingest.sh --timeout 36000`, wrapped in `caffeinate` so macOS does not
   sleep. Progress: `logs.sh` prints a line every 5 posts; `psql.sh --stats` shows the
   cursor and `last_ingest_write` moving.
6. **On a halt (fork 6a).** The channel report carries `posts_failed = 1` and
   `error: halted at post=<id>: extraction failed (<reason>)`. Re-run `ingest.sh` — it
   resumes at that post. The same post fails again → skip it by setting the cursor to
   that post's `published_at` (the Telegram offset is exclusive, so exactly that post is
   skipped) and record the post id in `progress.md`.
7. **Verify.** `verify.sh --timeout 36000`, no limit — every unverified row. Expected
   `failed = 0`.
8. **Timers back.** `timers.sh --install`.
9. **Record.** "After" snapshot; `progress.md` entry with before/after numbers;
   `runbook/rerun-channel.md` with every step and its expected output.

Prerequisite for step 4 and for the runbook: `deploy/window.sh` and
`tests/test_deploy_window.py` are untracked on the `docs/add-telegram-author` branch.
They are committed (green zone) before the runbook cites them.

### Expected size, cost and time

| Item | Estimate | Basis |
|---|---:|---|
| Posts to read | ~6k | 5572 in April 2026 plus five months of posts |
| Extraction cost | ~$6 | ≈$0.001 per post (eval v2) |
| Ingest time | ~7 h | 6.6 h for the same channel on 2026-07-15 |
| New predictions | 4–5k | 4177 today; DeepSeek had higher precision and less over-extraction |
| Verification cost | ~$5 | 2 Flash Lite calls per prediction |
| Verification time | ~5 h | ≈4 s per prediction |

## 5. What can go wrong

| Risk | What happens | Rule that handles it |
|---|---|---|
| The 05:00 tick starts a second cycle | duplicate predictions | timers removed in step 1; reinstall is step 8, not optional |
| A second `ingest.sh` by hand while the cycle still runs | duplicate predictions | operator rule: wait for the `done` line |
| Laptop sleeps or SSH drops | curl dies; the cycle keeps running on the box | `caffeinate`; if it drops anyway, monitor and do not restart |
| DeepSeek returns an unreadable answer | post → `failed`, channel halts, cursor stays | step 6 |
| DeepSeek or Gemini outage / rate limit | litellm retries 3×, then the post fails → halt | wait, re-run; nothing is lost |
| Telegram FloodWait on a 6k-post read | Telethon sleeps on short waits; a long one raises → channel `error` | re-run later; the cursor is intact |
| The delete removes the wrong rows | data loss | dump first; count checks inside the transaction; other authors' total must not change |
| Verify timer overlaps the manual verify run | rows verified twice | both timers off for the day |
| Bot has no Arestovich data for ~1 day | "nothing found" answers | accepted; 0 queries in 7 days |
| Old `raw_documents` whose posts now yield nothing | orphan document rows stay | harmless; `docs` in `--stats` may exceed docs with predictions |

## 6. What covers it

No code changes, so no new tests. Every step produces an output that is compared with an
expected value: row counts, `DELETE n`, `UPDATE 1`, report fields, log lines. The runbook
lists those expected outputs so the next rerun carries the same evidence.

## 7. Scope

**Not included:**

- Any change in `src/`: no concurrency lock, no `limit` for `/ingest/run`, no provenance
  column.
- Other channels' cursors (Podoliak's sits at 2026-02-12).
- The verifier, the planner and the answer generator (Gemini Flash Lite, hardcoded in
  `factory.py`) and embeddings (OpenAI).
- Re-verifying rows that already have a verdict; the recheck loop.
- `CLAUDE.md` line 69 — the stale sentence about the code default model. One-line fix,
  offered separately.
- The flip-flop PoC files that sit untracked on the same branch.
