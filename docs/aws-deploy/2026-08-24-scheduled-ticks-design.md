# Design — scheduled ingest/verify ticks on the AWS box

**Date:** 2026-08-24 · **Track:** `aws-deploy` · **Status:** designed
**Review card:** [`2026-08-24-scheduled-ticks-review-card.md`](2026-08-24-scheduled-ticks-review-card.md) — the card is binding; this doc is the reasoning behind it.
**Plan:** to be written (`2026-08-24-scheduled-ticks-plan.md`) — full script bodies and unit-file text live there, not here.

---

## 1. Why it exists

Prod already runs full cycles. It ran them for real: ingest 2026-07-15 (2238 documents),
verification 2026-07-16 (4116/4116, `failed=0`). But every one of those cycles started
because a human typed `./deploy/ingest.sh`. `progress.md` lists
"автоматизація/розклад інжесту" as the last open production item.

The cost of staying manual: the corpus goes stale between runs, so the Telegram bot and
`POST /answer` answer from old data; the unverified backlog grows silently; and the
channel cursor drifts further behind, which makes each eventual catch-up run bigger and
more expensive than a daily delta would have been.

**Goal:** the box drives its own daily rhythm — no laptop, no new AWS infrastructure,
and no way for one bad night to produce an unbounded LLM bill.

---

## 2. Scope — two independent ticks

**Why:** ingest and verify have different cost shapes. Ingest cost follows how much the
channel posted; verify cost is two LLM calls per prediction and is worth capping
separately. Chaining them into one job would force one cadence on both.

**What it gives:** two timers that can be re-timed, disabled or run by hand independently
of each other.

**Success criterion:** disabling one timer leaves the other armed and firing.

**Internals.** Both endpoints already exist and need no change:

| Tick | Call | Endpoint |
|------|------|----------|
| ingest | `POST localhost:8000/ingest/run` | `app.py:47` → `IngestionOrchestrator.run_cycle()` |
| verify | `POST localhost:8000/verify/run?limit=N` | `app.py:65` → `VerificationOrchestrator.run_cycle(limit=N)` |

Both are synchronous by design (`docs/ingestion-to-aws/2026-05-05-fastapi-http-trigger-design.md`, Q1):
the HTTP call blocks until the cycle finishes and returns the report. That is exactly
the shape a cron-style caller wants — the exit of the call *is* the end of the work, so
the tick needs no job-polling.

Port 8000 is bound to the box's loopback only. A tick that runs *on the box* therefore
needs no auth, no TLS and no security-group change.

---

## 3. Where the schedule lives, and how it reaches the box

**Why:** three hosts were possible — the box itself, EventBridge Scheduler + SSM, or a
launchd timer on the laptop. The laptop is out because it fires only while the laptop is
awake and its SSH ingress rule breaks whenever the home IP changes. EventBridge + SSM is
the most capable option (it could even start the env before a tick), but it costs a new
IAM role, the SSM agent, and an update to `prophet-compute`.

**What it gives:** a schedule that is independent of the laptop and adds nothing to the
AWS account — no new stack, role, or scheduled rule.

**Success criterion:** after install, `systemctl list-timers` on the box shows both
timers with a next-run time, and the CloudFormation stacks show no new events.

**Internals.** Five files on the box:

```
/usr/local/bin/prophet-tick.sh              # the tick itself (§5)
/etc/systemd/system/prophet-ingest.service  # what to run
/etc/systemd/system/prophet-ingest.timer    # when: 05:00 UTC daily
/etc/systemd/system/prophet-verify.service
/etc/systemd/system/prophet-verify.timer    # when: 06:00 UTC daily
```

They are installed **over SSH**, not through CloudFormation. This is the one decision in
this design that trades correctness for safety:

- Through `UserData` in `compute-stack.yaml` would be the declarative, self-healing way —
  a rebuilt box would get its timers for free.
- But that needs `update-stack` on `prophet-compute`, and `progress.md` carries an
  explicit warning about it: `LatestAmiId` resolves to the newest AL2023 while the live
  box runs an older one, so the update **replaces the instance**. That destroys the
  running box and the `tg_session.session` on its disk. Losing the box to install a cron
  job is not a trade worth making.

So `deploy/timers.sh --install` pushes the files over SSH, the same way
`deploy/refresh.sh` already pushes fresh secrets onto the live box (quoted heredoc →
`ssh ... bash -s`, `sudo` for anything under `/etc`).

**Known cost, accepted:** if `prophet-compute` is ever recreated, the new box comes up
with no timers until `--install` is run again. Recorded as a follow-up in §9.

---

## 4. Cadence and cost cap

**Why:** the whole point of a schedule is that it runs while nobody is watching, so the
worst case has to be bounded before it is switched on.

**What it gives:** a predictable daily maximum — one ingest over a one-day delta, plus at
most `limit` predictions verified.

**Success criterion:** on a normal day the two ticks together consume one day of channel
posts' worth of extraction calls and no more than `limit × 2` verification calls.

**Internals:**

| Tick | `OnCalendar` | Cap |
|------|--------------|-----|
| ingest | `*-*-* 05:00:00 UTC` | none — bounded by the channel cursor (`last_collected_at`) |
| verify | `*-*-* 06:00:00 UTC` | `?limit=50` |

The one-hour gap is not a correctness mechanism, just a convenience: a daily-delta ingest
normally finishes well inside an hour, so verify usually finds the lock free and picks up
predictions extracted the same morning. When ingest does run long, the lock (§5) makes
verify skip that day rather than compete with it.

Two honest limits of the cap:

- **Ingest is not capped by this design.** Its size is set by the cursor, and the cursor
  is moved by `deploy/window.sh`. A daily tick keeps the delta small precisely *because*
  it runs daily; after a long pause, the first tick is a catch-up run and is large. That
  is a property of the cursor, not of the schedule.
- **`limit` caps predictions, not money.** Fifty predictions is ~100 Flash Lite calls; the
  price per call is small but not fixed by us.

Changing either value means editing the unit and re-running `--install` (§6). There is no
runtime configuration for this — one place to look, no drift between a config file and
the installed unit.

---

## 5. The tick script — one lock, no catch-up

**Why:** an ingest cycle can run for many minutes. Two timers plus long cycles mean a
second cycle can start while the first is still working. The app has **no concurrency
lock** — `app.py` calls `run_cycle()` directly, and the original HTTP-trigger design
deferred locking explicitly ("no concurrency lock … we'll add it when a scheduled trigger
appears"). This design is that trigger, and it takes the lock outside the app instead.

**What it gives:** at most one cycle running on the box at a time, across both timers.

**Success criterion:** a second invocation while the first is running makes **no HTTP
call**, writes one "skipped" line, and exits 0 (a skip is normal operation, not a
failure).

**Internals.** One script serves both timers:

```
prophet-tick.sh <name> <url> <timeout-seconds>
```

- `name` — `ingest` / `verify`, used only for the log line.
- `url` — the full localhost URL including any query string.
- `timeout` — passed to `curl -m`; 3600 for ingest, 1800 for verify.

Behavior, in order:

1. Take `flock -n` on `/var/lock/prophet-tick.lock` — one lock file shared by both
   timers. Held → log `skipped: previous tick still running`, exit 0.
2. `curl -sS -m <timeout> -X POST <url>`, capturing the HTTP code separately from the
   body — the same `__HTTP__` marker technique `deploy/ingest.sh` already uses.
3. Write exactly one summary line to stdout, which systemd routes to journald.
4. Exit per the table in §7.

The lock is kernel-held and tied to the open file descriptor, so a reboot in the middle of
a cycle releases it. There is no stale lock to clean up. `flock` ships with util-linux on
Amazon Linux 2023; the plan's pre-check confirms it on the box before install.

**Missed ticks are not replayed** — `Persistent=false` on both timers. The env is paused
regularly to save credits (`runbook/stop-env.md`), and with `Persistent=true` the first
thing a freshly un-paused box would do is fire every missed tick — a catch-up ingest and
an LLM bill, seconds after `./deploy/start.sh`, while the user was only bringing the env
up to look at something. Skipping to the next 05:00 UTC is the less surprising behavior.

The units run the script as root (`Type=oneshot`), because the tick only needs `curl` to
loopback and a lock file, and root avoids a permissions question on `/var/lock`.

---

## 6. Visibility — `deploy/timers.sh`

**Why:** the box is SSH-only. There is no public HTTP, no log aggregator, and the IP
changes on every stop/start. Without a command, "did last night's tick run?" means an
ad-hoc SSH and remembering the unit names.

**What it gives:** one command that answers *are the timers armed, when do they fire next,
what happened on the last runs* — plus the install/uninstall path from §3.

**Success criterion:** immediately after `--install`, a bare `./deploy/timers.sh` prints
both timers with a next-run time and the result of their most recent run.

**Internals.** A sibling of `logs.sh` / `status.sh` / `refresh.sh`, following the family
conventions exactly: resolve the box dynamically by tag `Name=prophet-checker` + state
`running`, same `SSH_OPTS` defaults, `--dry-run` prints the remote block and touches
neither AWS nor SSH, `usage()` reads the header comment, config via env with defaults
(`REGION`, `SSH_KEY`, `SSH_USER`, `BOX_TAG`, `SSH_OPTS`).

```
./deploy/timers.sh                # read-only: timer state + last results   (default)
./deploy/timers.sh --tail 20      # more history per timer
./deploy/timers.sh --install      # push units, daemon-reload, enable --now
./deploy/timers.sh --uninstall    # disable --now, remove units, daemon-reload
./deploy/timers.sh --run ingest   # force one tick now (respects the lock)
./deploy/timers.sh --dry-run      # print the remote block, do nothing
```

Read-only is the default and needs no confirmation. `--install`, `--uninstall` and
`--run` change the box, so they confirm before acting and take `-y`, exactly like
`deploy.sh`, `ingest.sh` and `verify.sh` do. `--run ingest` mutates prod (writes to the
DB, spends LLM money) and says so in its prompt.

Remote reads are `systemctl list-timers` for state and `journalctl -u <unit>` for
results — both read-only.

---

## 7. Error table

| Situation | Tick behavior | Exit | What you see in `timers.sh` |
|-----------|---------------|------|------------------------------|
| Lock held by the other tick | no HTTP call | 0 | `skipped: previous tick still running` |
| HTTP 200 | cycle ran | 0 | one summary line with the report counts |
| HTTP 503 (orchestrator not ready) | box just booted, app still starting | 1 | unit `failed`, `503` in the line |
| HTTP 500 (cycle blew up) | body written to journal | 1 | unit `failed` + the app's detail |
| curl code 000 (app down / not on 8000) | connection never made | 1 | unit `failed`, hint to run `logs.sh` |
| Env paused (box stopped) | timer does not exist to fire | — | nothing logged for that day; `status.sh` says PAUSED |
| Laptop off / home IP changed | irrelevant — the tick is on the box | — | no effect on ticks; only `timers.sh` itself can't connect |

A failing unit stays visible: systemd records the failed state until the next successful
run, so a morning failure is still visible that evening.

---

## 8. Testing

**Why:** these scripts drive prod and spend money, and the repo already has a working
pattern for testing exactly this kind of script.

**What it gives:** the whole design is verifiable on the laptop before anything is
installed on the box.

**Success criterion:** `pytest tests/test_deploy_timers.py tests/test_box_tick.py` green
on macOS with no AWS credentials, no SSH, and no network.

**Internals.** Hermetic pytest with fake binaries on `PATH`, the same technique as
`tests/test_deploy_verify.py` (fake `aws`, fake `ssh` that records its argv):

- `tests/test_box_tick.py` — runs `prophet-tick.sh` directly with a fake `curl` and a fake
  `flock` on `PATH`. Cases: 200 → exit 0 + summary line; lock held → no curl call at all,
  exit 0; 503/500/000 → exit 1 with the code in the line; the timeout argument reaches
  `curl -m`.
- `tests/test_deploy_timers.py` — fake `aws`/`ssh`. Cases: default mode is read-only (the
  recorded remote command contains no `systemctl enable` and no `rm`); `--install` sends
  all five files and a `daemon-reload`; `--uninstall` disables before removing;
  `--dry-run` calls neither `aws` nor `ssh`; missing box → clear error, exit 2.

`flock` is faked because macOS has no `flock` — the test asserts that the script *asks*
for the lock correctly, not that the kernel honours it.

**Live proof, once installed** (goes in the plan as its own step): `systemd-analyze verify`
on the units, `timers.sh` showing both next-run times, and one forced
`--run verify` with the limit temporarily at 5 — a real cycle, small enough to be cheap.

---

## 9. Out of scope, and follow-ups

**Out of scope — deliberately:**

- **App-side concurrency lock** (409 on a second `POST /ingest/run`). `src/` is not touched
  by this work at all. The `flock` covers the scheduled path; a manual
  `./deploy/ingest.sh` from the laptop still bypasses it, and that is acceptable because a
  human running it is a human who knows what else is running.
- **EventBridge / SSM / any CloudFormation change** — see §3.
- **Telegram alerts on tick results.** Pull-based visibility first; push can be added later
  without changing anything here.
- **Verifier recheck-loop** (`next_check_at`, re-checking `premature`) — a separate parked
  track; these timers only drive first-pass verification.
- **Auto start/stop of the env around ticks**, and **GitHub Actions CI** — unrelated items
  from `progress.md`.

**Follow-ups worth recording:**

1. Next time `prophet-compute` is recreated on purpose **with a pinned AMI**, move the five
   files into `UserData` so a fresh box self-installs its timers, and reduce
   `timers.sh --install` to a repair tool.
2. If ingest ever grows past its one-hour head start on a regular basis, that is the signal
   to reconsider the 05:00/06:00 gap — not to widen the lock.
