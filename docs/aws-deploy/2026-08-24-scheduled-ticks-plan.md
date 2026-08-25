# Scheduled ingest/verify ticks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the AWS box runs its own daily ingest and verify cycles, on two systemd timers, without a laptop and without an unbounded LLM bill.

**Architecture:** implement per design [`2026-08-24-scheduled-ticks-design.md`](2026-08-24-scheduled-ticks-design.md) — do not re-argue its decisions. Five files live on the box (one tick script + two `.service` + two `.timer`); their source of truth is `deploy/box/` in this repo. `deploy/timers.sh` pushes them over SSH and reads their state back. `src/` is not touched.

**Tech Stack:** bash (POSIX-ish, bash 3.2-compatible), systemd timers, `flock` from util-linux, pytest with fake binaries on `PATH`.

## Global Constraints

- **`src/` must not change.** Any task that wants an app-side change is out of scope — stop and re-open the design.
- **bash 3.2 compatible** — `deploy/*.sh` runs on the user's macOS. No `${arr[@]}` under `set -u`, no bash-4 features (`declare -A`, `${x^^}`).
- **Tests are hermetic:** no network, no AWS credentials, no SSH, no Docker. Fake binaries on `PATH`, exactly as `tests/test_deploy_verify.py` does.
- **`flock` and `systemd` do not exist on macOS** — the tick script is tested against a fake `flock`; the units are tested as text.
- **Ruff line length 100**; run `.venv/bin/ruff check . && .venv/bin/ruff format .` before each commit that touches Python.
- **Commits:** conventional commits, subject in Ukrainian (`type(scope): subject`). One commit per task.
- **All-green zone** (shell, unit files, docs, tests) — no red-zone diff in this plan, so no line-by-line review gate between tasks.
- **Never commit** `.env` or `tg_session*`.
- Exact values from the design, copied verbatim: ingest `*-*-* 05:00:00 UTC`, timeout 3600; verify `*-*-* 06:00:00 UTC` with `?limit=50`, timeout 1800; lock file `/var/lock/prophet-tick.lock`; `Persistent=false` on both timers.

## File Structure

| File | Responsibility |
|------|----------------|
| `deploy/box/prophet-tick.sh` | Create — the tick: take the lock, POST, log one line |
| `deploy/box/prophet-ingest.service` | Create — what the ingest tick runs |
| `deploy/box/prophet-ingest.timer` | Create — when: 05:00 UTC daily |
| `deploy/box/prophet-verify.service` | Create — what the verify tick runs |
| `deploy/box/prophet-verify.timer` | Create — when: 06:00 UTC daily |
| `deploy/timers.sh` | Create — status / install / uninstall / run, over SSH |
| `tests/test_box_tick.py` | Create — tick behavior, fake `curl` + fake `flock` |
| `tests/test_box_units.py` | Create — unit files match the design's contract |
| `tests/test_deploy_timers.py` | Create — `timers.sh` modes, fake `aws` + fake `ssh` |
| `runbook/timers.md` | Create — operator runbook |
| `runbook/ingest.md`, `runbook/verify.md` | Modify — point at the new runbook |
| `progress.md`, `docs/aws-deploy/README.md` | Modify — close the "розклад інжесту" item |

---

### Task 1: The tick script

**Scope:** the single script both timers run on the box. It owns the lock, the HTTP call, and the one-line journald summary. Everything else in this plan depends on its argument contract.

**Files:**
- Create: `deploy/box/prophet-tick.sh`
- Test: `tests/test_box_tick.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: CLI contract `prophet-tick.sh <name> <url> <timeout-seconds>`, where `name` is `ingest` or `verify`. Exit codes: `0` = HTTP 200 **or** lock busy; `1` = cycle failed; `2` = bad arguments. Env override: `LOCK_FILE` (default `/var/lock/prophet-tick.lock`). Task 2's `ExecStart=` lines and Task 5's `--run` depend on exactly this.

- [x] **Step 1: Write the failing test**

Create `tests/test_box_tick.py`:

```python
"""Tests for deploy/box/prophet-tick.sh — one scheduled cycle on the box.

Hermetic: fake `curl` and fake `flock` on PATH. The fake `curl` records its argv and
echoes a canned report plus the `__HTTP__` marker the script parses; the fake `flock`
either runs the wrapped command or simulates a busy lock by exiting with the script's
conflict code. macOS has no real flock, so the assertion is "the script asks for the
lock correctly", not "the kernel honours it".
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TICK_SH = REPO / "deploy" / "box" / "prophet-tick.sh"

INGEST_BODY = (
    '{"started_at":"2026-08-24T05:00:00Z","finished_at":"2026-08-24T05:04:00Z",'
    '"channels_processed":['
    '{"person_source_id":"@arestovich","posts_seen":40,"posts_with_predictions":5,'
    '"posts_failed":1,"predictions_extracted":6},'
    '{"person_source_id":"@M_Podolyak","posts_seen":2,"posts_with_predictions":1,'
    '"posts_failed":0,"predictions_extracted":1}]}'
)

VERIFY_BODY = (
    '{"started_at":"2026-08-24T06:00:00Z","verified":12,"failed":0,"skipped":3,'
    '"entries":[{"prediction_id":"p1","status":"confirmed"}]}'
)

FAKE_CURL = r"""#!/usr/bin/env bash
# fake curl — record argv, echo a canned body plus the __HTTP__ marker the real
# `curl -w` would append.
[ -n "${FAKE_CURL_LOG:-}" ] && printf '%s\n' "$@" >> "$FAKE_CURL_LOG"
body="${FAKE_CURL_BODY:-}"
[ -n "$body" ] || body='{}'
printf '%s\n' "$body"
printf '__HTTP__ %s\n' "${FAKE_CURL_CODE:-200}"
exit 0
"""

FAKE_FLOCK = r"""#!/usr/bin/env bash
# fake flock — argv is: -n -E <code> <lockfile> <cmd> <args...>
[ -n "${FAKE_FLOCK_LOG:-}" ] && printf '%s\n' "$@" >> "$FAKE_FLOCK_LOG"
if [ "${FAKE_FLOCK_BUSY:-0}" = "1" ]; then
  exit "$3"          # the conflict code the caller asked for via -E
fi
shift 4              # drop -n -E <code> <lockfile>, leaving the wrapped command
exec "$@"
"""


def _write_exec(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exec(bin_dir / "curl", FAKE_CURL)
    _write_exec(bin_dir / "flock", FAKE_FLOCK)
    curl_log = tmp_path / "curl.log"
    flock_log = tmp_path / "flock.log"
    base = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_CURL_LOG": str(curl_log),
        "FAKE_FLOCK_LOG": str(flock_log),
        "LOCK_FILE": str(tmp_path / "tick.lock"),
    }
    return {"env": base, "curl_log": curl_log, "flock_log": flock_log}


def run_tick(env, *args, **overrides):
    e = {**env["env"], **overrides}
    return subprocess.run(
        ["bash", str(TICK_SH), *args], env=e, capture_output=True, text=True
    )


def test_ingest_200_logs_one_summary_line(env):
    proc = run_tick(
        env,
        "ingest",
        "http://localhost:8000/ingest/run",
        "3600",
        FAKE_CURL_BODY=INGEST_BODY,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, proc.stdout
    assert "tick=ingest ok http=200" in lines[0]
    assert "channels=2" in lines[0]
    assert "posts=42" in lines[0]
    assert "predictions=7" in lines[0]
    assert "post_failures=1" in lines[0]


def test_verify_200_logs_report_counts(env):
    proc = run_tick(
        env,
        "verify",
        "http://localhost:8000/verify/run?limit=50",
        "1800",
        FAKE_CURL_BODY=VERIFY_BODY,
    )
    assert proc.returncode == 0, proc.stderr
    assert "tick=verify ok http=200 verified=12 failed=0 skipped=3" in proc.stdout


def test_busy_lock_skips_without_calling_curl(env):
    proc = run_tick(
        env,
        "ingest",
        "http://localhost:8000/ingest/run",
        "3600",
        FAKE_FLOCK_BUSY="1",
    )
    assert proc.returncode == 0, proc.stderr
    assert "skipped: previous tick still running" in proc.stdout
    assert not env["curl_log"].exists(), "a skipped tick must make no HTTP call"


def test_timeout_argument_reaches_curl(env):
    run_tick(env, "verify", "http://localhost:8000/verify/run?limit=50", "1800")
    recorded = env["curl_log"].read_text()
    assert "-m\n1800" in recorded
    assert "http://localhost:8000/verify/run?limit=50" in recorded


def test_lock_file_is_shared_by_both_ticks(env):
    run_tick(env, "ingest", "http://localhost:8000/ingest/run", "3600")
    run_tick(env, "verify", "http://localhost:8000/verify/run?limit=50", "1800")
    asked = env["flock_log"].read_text()
    lock = env["env"]["LOCK_FILE"]
    assert asked.count(lock) == 2, "both ticks must contend for the same lock file"
    assert "-n" in asked, "must not wait on a busy lock"


@pytest.mark.parametrize("code", ["503", "500", "000"])
def test_non_200_fails_with_the_code(env, code):
    proc = run_tick(
        env,
        "verify",
        "http://localhost:8000/verify/run?limit=50",
        "1800",
        FAKE_CURL_CODE=code,
    )
    assert proc.returncode == 1
    assert f"tick=verify FAILED http={code}" in proc.stderr


def test_missing_arguments_exit_2(env):
    proc = run_tick(env, "ingest")
    assert proc.returncode == 2
    assert "usage:" in proc.stderr
    assert not env["curl_log"].exists()
```

- [x] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_box_tick.py -q`
Expected: FAIL — every test errors because `deploy/box/prophet-tick.sh` does not exist.

- [x] **Step 3: Write the script**

Create `deploy/box/prophet-tick.sh`:

```bash
#!/usr/bin/env bash
#
# prophet-tick.sh — один запланований цикл на боксі (ingest або verify).
#
# Живе на боксі як /usr/local/bin/prophet-tick.sh; запускається юнітами
# prophet-ingest.service / prophet-verify.service. Бере СПІЛЬНИЙ flock (обидва
# таймери — один лок-файл), б'є POST у localhost:8000 і пише рівно один рядок
# підсумку в stdout (systemd → journald).
#
# Usage: prophet-tick.sh <name> <url> <timeout-seconds>
#   name    — ingest | verify (йде в рядок логу)
#   url     — повний localhost-URL разом із query (?limit=50)
#   timeout — секунди, передається в curl -m
#
# Exit: 0 — цикл пройшов (HTTP 200) АБО тік пропущено (лок зайнятий — це норма).
#       1 — цикл не пройшов (не-200 або curl не достукався).
#       2 — погані аргументи.
#
# Конфіг через env: LOCK_FILE (дефолт /var/lock/prophet-tick.lock).

set -uo pipefail

LOCK_FILE="${LOCK_FILE:-/var/lock/prophet-tick.lock}"
# flock -E: окремий код на «лок зайнятий», інакше не відрізнити від exit 1 команди.
CONFLICT_CODE=99

name="${1:-}"
url="${2:-}"
timeout="${3:-}"

if [ -z "$name" ] || [ -z "$url" ] || [ -z "$timeout" ]; then
  echo "usage: prophet-tick.sh <name> <url> <timeout-seconds>" >&2
  exit 2
fi

# Перший захід перезапускає себе під flock; другий (PROPHET_TICK_LOCKED=1) уже з локом.
if [ "${PROPHET_TICK_LOCKED:-0}" != "1" ]; then
  export PROPHET_TICK_LOCKED=1
  flock -n -E "$CONFLICT_CODE" "$LOCK_FILE" "$0" "$name" "$url" "$timeout"
  rc=$?
  if [ "$rc" = "$CONFLICT_CODE" ]; then
    echo "tick=$name skipped: previous tick still running"
    exit 0
  fi
  exit "$rc"
fi

# --- звідси лок наш ---

# Сума одного числового поля по всьому тілу: "posts_seen":40 + "posts_seen":2 → 42.
# Без jq/python — на боксі гарантовані лише coreutils.
sum_field() {
  printf '%s' "$2" | grep -o "\"$1\":[0-9]*" | cut -d: -f2 | awk '{ s += $1 } END { print s + 0 }'
}

summarize() {
  local body="$2"
  if [ "$1" = "verify" ]; then
    printf 'verified=%s failed=%s skipped=%s' \
      "$(sum_field verified "$body")" \
      "$(sum_field failed "$body")" \
      "$(sum_field skipped "$body")"
    return
  fi
  local channels
  # grep -c рахує РЯДКИ, а тіло приходить одним рядком → рахуємо входження.
  channels="$(printf '%s' "$body" | grep -o '"person_source_id"' | wc -l | tr -d ' ')"
  printf 'channels=%s posts=%s predictions=%s post_failures=%s' \
    "$channels" \
    "$(sum_field posts_seen "$body")" \
    "$(sum_field predictions_extracted "$body")" \
    "$(sum_field posts_failed "$body")"
}

raw="$(curl -sS -m "$timeout" -w '\n__HTTP__ %{http_code}\n' -X POST "$url" 2>&1)"
code="$(printf '%s\n' "$raw" | sed -n 's/^__HTTP__ //p' | tail -1)"
body="$(printf '%s\n' "$raw" | sed '/^__HTTP__ /d')"

if [ "$code" = "200" ]; then
  echo "tick=$name ok http=200 $(summarize "$name" "$body")"
  exit 0
fi

# Не-200: тіло в один рядок і обрізане — journald не місце для повного дампу.
echo "tick=$name FAILED http=${code:-none} body=$(printf '%s' "$body" | tr '\n' ' ' | cut -c1-300)" >&2
exit 1
```

- [x] **Step 4: Make it executable and run the tests**

```bash
chmod +x deploy/box/prophet-tick.sh
.venv/bin/python -m pytest tests/test_box_tick.py -q
```

Expected: 9 passed (7 tests, one of them parametrized ×3).

- [x] **Step 5: Commit**

```bash
git add deploy/box/prophet-tick.sh tests/test_box_tick.py
git commit -m "feat(deploy): тік-скрипт на боксі — flock + POST + рядок у journald"
```

---

### Task 2: The four systemd units

**Scope:** the schedule itself — when each tick fires and with which arguments. Pure configuration, but it carries the design's exact constants, so it gets a text-level regression test.

**Files:**
- Create: `deploy/box/prophet-ingest.service`, `deploy/box/prophet-ingest.timer`, `deploy/box/prophet-verify.service`, `deploy/box/prophet-verify.timer`
- Test: `tests/test_box_units.py`

**Interfaces:**
- Consumes: the CLI contract from Task 1 (`prophet-tick.sh <name> <url> <timeout>`).
- Produces: unit names `prophet-ingest.{service,timer}` and `prophet-verify.{service,timer}`, installed to `/etc/systemd/system/`. Tasks 4 and 5 refer to these names literally.

- [x] **Step 1: Write the failing test**

Create `tests/test_box_units.py`:

```python
"""Tests for deploy/box/*.service and *.timer — the schedule's constants.

These files are configuration, not code, but they carry the numbers the design fixed
(05:00/06:00 UTC, limit=50, timeouts, Persistent=false). A text test is what stops a
later edit from silently drifting away from the approved card.
"""

from pathlib import Path

import pytest

BOX = Path(__file__).resolve().parents[1] / "deploy" / "box"


def unit(name: str) -> str:
    return (BOX / name).read_text()


@pytest.mark.parametrize("name", ["prophet-ingest", "prophet-verify"])
def test_service_is_oneshot_with_explicit_timeout(name):
    text = unit(f"{name}.service")
    assert "Type=oneshot" in text
    # Explicit, not inherited: a hung curl must not hold the shared lock forever.
    assert "TimeoutStartSec=" in text


@pytest.mark.parametrize("name", ["prophet-ingest", "prophet-verify"])
def test_timer_fires_daily_in_utc_and_never_catches_up(name):
    text = unit(f"{name}.timer")
    assert "Persistent=false" in text, "an un-paused env must not replay missed ticks"
    assert "WantedBy=timers.target" in text
    assert "UTC" in text


def test_ingest_unit_matches_the_design_constants():
    assert (
        "ExecStart=/usr/local/bin/prophet-tick.sh ingest "
        "http://localhost:8000/ingest/run 3600" in unit("prophet-ingest.service")
    )
    assert "OnCalendar=*-*-* 05:00:00 UTC" in unit("prophet-ingest.timer")


def test_verify_unit_carries_the_cost_cap():
    assert (
        "ExecStart=/usr/local/bin/prophet-tick.sh verify "
        "http://localhost:8000/verify/run?limit=50 1800" in unit("prophet-verify.service")
    )
    assert "OnCalendar=*-*-* 06:00:00 UTC" in unit("prophet-verify.timer")


def test_service_start_timeout_exceeds_the_curl_timeout():
    for name, curl_timeout in (("prophet-ingest", 3600), ("prophet-verify", 1800)):
        line = [
            ln for ln in unit(f"{name}.service").splitlines()
            if ln.startswith("TimeoutStartSec=")
        ]
        assert len(line) == 1, name
        assert int(line[0].split("=", 1)[1]) > curl_timeout, name
```

- [x] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_box_units.py -q`
Expected: FAIL — `FileNotFoundError` on `deploy/box/prophet-ingest.service`.

- [x] **Step 3: Write the four unit files**

`deploy/box/prophet-ingest.service`:

```ini
[Unit]
Description=prophet-checker — one ingestion cycle
After=docker.service
Wants=docker.service

[Service]
Type=oneshot
# Трохи більше за curl -m 3600: зависла спроба не має тримати спільний лок вічно.
TimeoutStartSec=3900
ExecStart=/usr/local/bin/prophet-tick.sh ingest http://localhost:8000/ingest/run 3600
```

`deploy/box/prophet-ingest.timer`:

```ini
[Unit]
Description=prophet-checker — daily ingestion tick (05:00 UTC)

[Timer]
OnCalendar=*-*-* 05:00:00 UTC
# Persistent=false: після паузи env не добиваємо пропущені тіки — інакше підйом
# боксу відразу палить LLM-гроші (див. design §5).
Persistent=false
AccuracySec=1min

[Install]
WantedBy=timers.target
```

`deploy/box/prophet-verify.service`:

```ini
[Unit]
Description=prophet-checker — one verification cycle
After=docker.service
Wants=docker.service

[Service]
Type=oneshot
TimeoutStartSec=2100
ExecStart=/usr/local/bin/prophet-tick.sh verify http://localhost:8000/verify/run?limit=50 1800
```

`deploy/box/prophet-verify.timer`:

```ini
[Unit]
Description=prophet-checker — daily verification tick (06:00 UTC)

[Timer]
OnCalendar=*-*-* 06:00:00 UTC
Persistent=false
AccuracySec=1min

[Install]
WantedBy=timers.target
```

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_box_units.py -q`
Expected: 8 passed.

- [x] **Step 5: Commit**

```bash
git add deploy/box/prophet-ingest.service deploy/box/prophet-ingest.timer \
        deploy/box/prophet-verify.service deploy/box/prophet-verify.timer \
        tests/test_box_units.py
git commit -m "feat(deploy): systemd-юніти розкладу — 05:00 інжест, 06:00 верифікація UTC"
```

---

### Task 3: `deploy/timers.sh` — read-only status

**Scope:** the default, safe half of the operator command: resolve the box and print whether the timers are armed, when they fire next, and how the last runs ended. Mutating modes come in Tasks 4 and 5.

**Files:**
- Create: `deploy/timers.sh`
- Test: `tests/test_deploy_timers.py`

**Interfaces:**
- Consumes: unit names from Task 2.
- Produces: `deploy/timers.sh` with `--tail N`, `-n|--dry-run`, `-h|--help`; env config `REGION`, `SSH_KEY`, `SSH_USER`, `BOX_TAG`, `SSH_OPTS`, `BOX_DIR` (default `deploy/box`). Tasks 4 and 5 extend the same argument parser and the same `case "$MODE"` block.

Note the deliberate flag choice: `-n` means `--dry-run` here (as in `ingest.sh`, `verify.sh`, `refresh.sh`), **not** `--tail` as in `logs.sh`. `--tail` has no short alias.

- [x] **Step 1: Write the failing test**

Create `tests/test_deploy_timers.py`:

```python
"""Tests for deploy/timers.sh — schedule state on the live box, over SSH.

Hermetic: fake `aws` and `ssh` on PATH, following tests/test_deploy_verify.py. The fake
`ssh` records its full argv, so the remote command string is assertable without a
connection. Default mode must be read-only — that is the property most worth locking in.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TIMERS_SH = REPO / "deploy" / "timers.sh"

FAKE_AWS = r"""#!/usr/bin/env bash
# fake aws — first ec2 call resolves the instance id, the --instance-ids call the IP
svc="$1"
scen="${FAKE_AWS_SCENARIO:-running}"
if [ "$svc" = "ec2" ]; then
  if printf '%s ' "$@" | grep -q -- '--instance-ids'; then
    echo "63.185.91.181"
  else
    [ "$scen" = "nobox" ] || echo "i-0b2811b6"
  fi
fi
exit 0
"""

FAKE_SSH = r"""#!/usr/bin/env bash
# fake ssh — record argv (the last element is the remote command string), swallow any
# stdin (the install tar), and succeed.
[ -n "${FAKE_SSH_LOG:-}" ] && printf '%s\n' "$@" >> "$FAKE_SSH_LOG"
[ -n "${FAKE_SSH_STDIN:-}" ] && cat > "$FAKE_SSH_STDIN"
echo "(fake remote output)"
exit 0
"""


def _write_exec(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exec(bin_dir / "aws", FAKE_AWS)
    _write_exec(bin_dir / "ssh", FAKE_SSH)
    key = tmp_path / "prophet-checker-key.pem"
    key.write_text("dummy")
    ssh_log = tmp_path / "ssh.log"
    base = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SSH_KEY": str(key),
        "FAKE_SSH_LOG": str(ssh_log),
    }
    return {"env": base, "ssh_log": ssh_log, "tmp": tmp_path}


def run_timers(env, *args, aws_scenario="running", stdin=None, **overrides):
    e = {**env["env"], "FAKE_AWS_SCENARIO": aws_scenario, **overrides}
    return subprocess.run(
        ["bash", str(TIMERS_SH), *args],
        env=e,
        capture_output=True,
        text=True,
        input=stdin,
    )


def _remote(env) -> str:
    return env["ssh_log"].read_text() if env["ssh_log"].exists() else ""


def test_default_mode_reads_timer_state(env):
    proc = run_timers(env)
    assert proc.returncode == 0, proc.stderr
    remote = _remote(env)
    assert "list-timers" in remote
    assert "journalctl" in remote
    assert "prophet-ingest" in remote and "prophet-verify" in remote


def test_default_mode_is_read_only(env):
    run_timers(env)
    remote = _remote(env)
    for forbidden in ("systemctl enable", "systemctl start", "rm -f", "install -m"):
        assert forbidden not in remote, f"read-only mode must not run: {forbidden}"


def test_tail_reaches_journalctl(env):
    run_timers(env, "--tail", "25")
    assert "-n 25" in _remote(env)


def test_bad_tail_dies_before_ssh(env):
    proc = run_timers(env, "--tail", "abc")
    assert proc.returncode == 2
    assert not env["ssh_log"].exists()


def test_dry_run_prints_plan_without_ssh(env):
    proc = run_timers(env, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "list-timers" in proc.stdout
    assert not env["ssh_log"].exists(), "dry-run must not SSH"


def test_no_running_box_dies_before_ssh(env):
    proc = run_timers(env, aws_scenario="nobox")
    assert proc.returncode != 0
    assert "stop-env" in proc.stderr
    assert not env["ssh_log"].exists()


def test_help_does_not_touch_aws_or_ssh(env):
    proc = run_timers(env, "--help")
    assert proc.returncode == 0
    assert "timers.sh" in proc.stdout
    assert not env["ssh_log"].exists()
```

- [x] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_deploy_timers.py -q`
Expected: FAIL — `deploy/timers.sh` does not exist.

- [x] **Step 3: Write the script**

Create `deploy/timers.sh`:

```bash
#!/usr/bin/env bash
#
# timers.sh — розклад інжесту/верифікації на живому AWS-боксі: стан, установка, зняття.
#
# Резолвить бокс (tag Name=prophet-checker, running) → SSH → systemd. За замовчуванням
# READ-ONLY: показує, чи заряджені таймери, коли наступний запуск і чим закінчились
# останні. Побратими: status.sh, logs.sh, refresh.sh.
#
# Приклади:
#   ./deploy/timers.sh                 # стан + останні результати (read-only)
#   ./deploy/timers.sh --tail 25       # більше історії на таймер
#   ./deploy/timers.sh --dry-run       # надрукувати план, нічого не робити
#
# Конфіг через env (є дефолти): REGION, SSH_KEY, SSH_USER, BOX_TAG, SSH_OPTS, BOX_DIR.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGION="${REGION:-eu-central-1}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/prophet-checker-key.pem}"
SSH_USER="${SSH_USER:-ec2-user}"
BOX_TAG="${BOX_TAG:-prophet-checker}"
# ServerAliveInterval: --run ingest мовчить хвилинами — keepalive, щоб SSH не рвався.
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8 -o ServerAliveInterval=30}"
BOX_DIR="${BOX_DIR:-$HERE/box}"

MODE="status"
TAIL="10"
DRY_RUN=0

usage() { sed -n '3,17p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "ERROR: $*" >&2; exit 2; }

# --- аргументи ---
while [ $# -gt 0 ]; do
  case "$1" in
    --tail)       shift; TAIL="${1:-}" ;;
    --tail=*)     TAIL="${1#*=}" ;;
    -n|--dry-run) DRY_RUN=1 ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

printf '%s' "$TAIL" | grep -Eq '^[1-9][0-9]*$' || die "--tail має бути додатнім цілим: '$TAIL'"

# --- віддалений блок: лише читання (list-timers + journalctl) ---
# Одинарні лапки — нічого не розкривається локально; $TAIL вставляємо конкатенацією.
REMOTE_STATUS='echo "== timers =="
systemctl list-timers --all --no-pager "prophet-*.timer" || true
for u in prophet-ingest prophet-verify; do
  echo
  echo "== $u ($(systemctl is-enabled "$u.timer" 2>/dev/null || echo not-installed)) =="
  sudo journalctl -u "$u.service" --no-pager -o short-iso -n '"$TAIL"' 2>/dev/null || echo "(нема записів)"
done'

# --- dry-run: надрукувати й вийти (без AWS/SSH, працює будь-де) ---
if [ "$DRY_RUN" -eq 1 ]; then
  echo "# DRY RUN — нічого не виконується"
  echo "# region=$REGION  box_tag=$BOX_TAG  ssh_user=$SSH_USER  mode=$MODE  tail=$TAIL"
  echo "# резолв боксу: aws ec2 describe-instances (Name=$BOX_TAG, state=running) → IP"
  echo "# --- віддалений блок ---"
  printf '%s\n' "$REMOTE_STATUS"
  exit 0
fi

# --- preflight ---
command -v aws >/dev/null || die "нема aws CLI"
command -v ssh >/dev/null || die "нема ssh"
[ -f "$SSH_KEY" ] || die "нема SSH-ключа: $SSH_KEY (задай через SSH_KEY=...)"

# --- резолв боксу (як deploy.sh/logs.sh: describe-instances → id → IP) ---
BOX="$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$BOX_TAG" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
{ [ -n "$BOX" ] && [ "$BOX" != "None" ]; } || \
  die "нема живого боксу '$BOX_TAG'. Env на паузі? Див. runbook/stop-env.md «Підйом»."
[ "$(printf '%s' "$BOX" | wc -w)" -eq 1 ] || die "кілька живих боксів: $BOX — не вгадую."

IP="$(aws ec2 describe-instances --region "$REGION" --instance-ids "$BOX" \
  --query 'Reservations[].Instances[].PublicIpAddress' --output text)"
{ [ -n "$IP" ] && [ "$IP" != "None" ]; } || die "у боксу $BOX нема публічного IP."
echo "box=$BOX  ip=$IP"

# --- виконання ---
# shellcheck disable=SC2086
ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$IP" "$REMOTE_STATUS"
```

- [x] **Step 4: Make it executable and run the tests**

```bash
chmod +x deploy/timers.sh
.venv/bin/python -m pytest tests/test_deploy_timers.py -q
```

Expected: 7 passed.

- [x] **Step 5: Commit**

```bash
git add deploy/timers.sh tests/test_deploy_timers.py
git commit -m "feat(deploy): timers.sh — read-only стан розкладу на боксі"
```

---

### Task 4: `--install` / `--uninstall`

**Scope:** get the five files onto the box and arm the timers — and take them off again. This is the mutating half, so it confirms before acting.

**Files:**
- Modify: `deploy/timers.sh`
- Test: `tests/test_deploy_timers.py` (append)

**Interfaces:**
- Consumes: `deploy/box/` contents from Tasks 1–2; the parser and box-resolution from Task 3.
- Produces: `--install`, `--uninstall`, `-y|--yes`. Files land as `/usr/local/bin/prophet-tick.sh` (0755) and `/etc/systemd/system/prophet-{ingest,verify}.{service,timer}` (0644).

The payload is streamed as a tar over the SSH connection's stdin, so the unit text lives in exactly one place — `deploy/box/` — and cannot drift from what the tests in Task 2 check.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_deploy_timers.py`:

```python
def test_install_pushes_units_and_arms_timers(env):
    stdin_log = env["tmp"] / "ssh_stdin.bin"
    proc = run_timers(env, "--install", "-y", FAKE_SSH_STDIN=str(stdin_log))
    assert proc.returncode == 0, proc.stderr
    remote = _remote(env)
    assert "install -m 755" in remote
    assert "/usr/local/bin/prophet-tick.sh" in remote
    assert "install -m 644" in remote
    assert "/etc/systemd/system/" in remote
    assert "systemctl daemon-reload" in remote
    assert "enable --now prophet-ingest.timer prophet-verify.timer" in remote
    assert stdin_log.exists() and stdin_log.stat().st_size > 0, "units must be streamed"


def test_install_payload_contains_every_box_file(env):
    stdin_log = env["tmp"] / "ssh_stdin.bin"
    run_timers(env, "--install", "-y", FAKE_SSH_STDIN=str(stdin_log))
    payload = stdin_log.read_bytes()
    for name in (
        b"prophet-tick.sh",
        b"prophet-ingest.service",
        b"prophet-ingest.timer",
        b"prophet-verify.service",
        b"prophet-verify.timer",
    ):
        assert name in payload, name


def test_uninstall_disables_before_removing(env):
    proc = run_timers(env, "--uninstall", "-y")
    assert proc.returncode == 0, proc.stderr
    remote = _remote(env)
    assert "disable --now" in remote
    assert "rm -f" in remote
    assert remote.index("disable --now") < remote.index("rm -f")
    assert "systemctl daemon-reload" in remote


def test_install_without_yes_and_no_input_cancels(env):
    proc = run_timers(env, "--install", stdin="")
    assert proc.returncode == 0, proc.stderr
    assert not env["ssh_log"].exists(), "an unconfirmed install must not SSH"


def test_install_with_missing_box_dir_dies(env):
    proc = run_timers(env, "--install", "-y", BOX_DIR=str(env["tmp"] / "nope"))
    assert proc.returncode == 2
    assert not env["ssh_log"].exists()
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_deploy_timers.py -q`
Expected: FAIL — the new tests fail with `unknown arg: --install` (exit 2).

- [x] **Step 3: Extend the script**

In `deploy/timers.sh`, add `ASSUME_YES=0` next to the other mode variables:

```bash
MODE="status"
TAIL="10"
DRY_RUN=0
ASSUME_YES=0
```

Add three cases to the argument parser, above the `-h|--help` line:

```bash
    --install)    MODE="install" ;;
    --uninstall)  MODE="uninstall" ;;
    -y|--yes)     ASSUME_YES=1 ;;
```

Add the two remote blocks directly after `REMOTE_STATUS`:

```bash
# Юніти їдуть таром у stdin SSH-зʼєднання: текст юнітів лишається в одному місці
# (deploy/box/) і не може розійтись із тим, що перевіряють тести.
REMOTE_INSTALL='set -euo pipefail
tmp="$(mktemp -d)"
trap "rm -rf $tmp" EXIT
tar -C "$tmp" -xf -
sudo install -m 755 "$tmp/prophet-tick.sh" /usr/local/bin/prophet-tick.sh
sudo install -m 644 "$tmp/prophet-ingest.service" "$tmp/prophet-ingest.timer" \
  "$tmp/prophet-verify.service" "$tmp/prophet-verify.timer" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prophet-ingest.timer prophet-verify.timer
systemctl list-timers --all --no-pager "prophet-*.timer"'

REMOTE_UNINSTALL='set -uo pipefail
sudo systemctl disable --now prophet-ingest.timer prophet-verify.timer || true
sudo rm -f /etc/systemd/system/prophet-ingest.service /etc/systemd/system/prophet-ingest.timer \
  /etc/systemd/system/prophet-verify.service /etc/systemd/system/prophet-verify.timer \
  /usr/local/bin/prophet-tick.sh
sudo systemctl daemon-reload
echo "юніти прибрано"'
```

Pick the block for the chosen mode, right before the dry-run branch:

```bash
case "$MODE" in
  status)    REMOTE="$REMOTE_STATUS" ;;
  install)   REMOTE="$REMOTE_INSTALL" ;;
  uninstall) REMOTE="$REMOTE_UNINSTALL" ;;
esac
```

and change the dry-run branch to print `"$REMOTE"` instead of `"$REMOTE_STATUS"`.

Add the install preflight after the generic preflight block:

```bash
if [ "$MODE" = "install" ]; then
  [ -d "$BOX_DIR" ] || die "нема каталогу з юнітами: $BOX_DIR (задай через BOX_DIR=...)"
  [ -f "$BOX_DIR/prophet-tick.sh" ] || die "у $BOX_DIR нема prophet-tick.sh"
fi
```

Add the confirmation after the box is resolved (`echo "box=$BOX  ip=$IP"`):

```bash
# --- підтвердження для мутуючих режимів ---
if [ "$MODE" != "status" ] && [ "$ASSUME_YES" -eq 0 ]; then
  printf 'Режим %s на боксі %s (%s)? [y/N] ' "$MODE" "$BOX" "$IP"
  read -r ans || ans=""
  case "$ans" in y|Y|yes|YES|Yes) ;; *) echo "скасовано."; exit 0 ;; esac
fi
```

Replace the single execution line with a per-mode dispatch:

```bash
# --- виконання ---
# shellcheck disable=SC2086
case "$MODE" in
  install)
    tar -C "$BOX_DIR" -cf - . | ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$IP" "$REMOTE"
    echo "✅ таймери встановлено й заряджено на боксі $BOX ($IP)"
    ;;
  *)
    ssh $SSH_OPTS -i "$SSH_KEY" "$SSH_USER@$IP" "$REMOTE"
    ;;
esac
```

Finally extend the header comment's examples (the `usage()` window is lines 3–17, so keep the added lines inside it, widening the `sed -n` range if you add more):

```bash
#   ./deploy/timers.sh --install       # залити юніти з deploy/box/ і зарядити таймери
#   ./deploy/timers.sh --uninstall     # зняти таймери й прибрати юніти
```

- [x] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_deploy_timers.py -q`
Expected: 12 passed. If `test_help_does_not_touch_aws_or_ssh` fails, the `usage()` line range no longer covers the header — widen the `sed -n '3,NN p'` window.

- [x] **Step 5: Commit**

```bash
git add deploy/timers.sh tests/test_deploy_timers.py
git commit -m "feat(deploy): timers.sh --install/--uninstall — юніти по SSH, без CFN"
```

---

### Task 5: `--run <name>`

**Scope:** force one tick immediately, without waiting for 05:00 — the way you smoke-test the schedule after installing it, and the way you re-run a failed night.

**Files:**
- Modify: `deploy/timers.sh`
- Test: `tests/test_deploy_timers.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 3–4.
- Produces: `--run ingest` / `--run verify` (also `--run=ingest`). Runs `systemctl start prophet-<name>.service`, which blocks until the oneshot finishes, then tails that unit's journal and exits with the unit's own result.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_deploy_timers.py`:

```python
@pytest.mark.parametrize("name", ["ingest", "verify"])
def test_run_starts_the_named_unit_and_tails_it(env, name):
    proc = run_timers(env, "--run", name, "-y")
    assert proc.returncode == 0, proc.stderr
    remote = _remote(env)
    assert f"systemctl start prophet-{name}.service" in remote
    assert f"journalctl -u prophet-{name}.service" in remote


def test_run_accepts_equals_form(env):
    run_timers(env, "--run=verify", "-y")
    assert "systemctl start prophet-verify.service" in _remote(env)


def test_run_rejects_an_unknown_name_before_ssh(env):
    proc = run_timers(env, "--run", "everything", "-y")
    assert proc.returncode == 2
    assert "ingest" in proc.stderr and "verify" in proc.stderr
    assert not env["ssh_log"].exists()


def test_run_without_a_name_dies(env):
    proc = run_timers(env, "--run", "-y")
    assert proc.returncode == 2
    assert not env["ssh_log"].exists()
```

Note on the last test: `--run -y` consumes `-y` as the name, which is not `ingest`/`verify`, so validation rejects it — the outcome the test asserts.

- [x] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_deploy_timers.py -q`
Expected: FAIL — `unknown arg: --run`.

- [x] **Step 3: Extend the script**

Add `RUN_NAME=""` next to the other mode variables. Add to the parser, above `-h|--help`:

```bash
    --run)        MODE="run"; shift; RUN_NAME="${1:-}" ;;
    --run=*)      MODE="run"; RUN_NAME="${1#*=}" ;;
```

Add validation next to the `--tail` check:

```bash
if [ "$MODE" = "run" ]; then
  case "$RUN_NAME" in
    ingest|verify) ;;
    *) die "--run приймає лише 'ingest' або 'verify' (дано: '${RUN_NAME:-<порожньо>}')" ;;
  esac
fi
```

Add the remote block after `REMOTE_UNINSTALL`:

```bash
# systemctl start на Type=oneshot блокується до кінця циклу, тож журнал після нього
# уже містить рядок підсумку. Код юніта пробрасуємо назад.
REMOTE_RUN='set -uo pipefail
sudo systemctl start prophet-'"$RUN_NAME"'.service; rc=$?
sudo journalctl -u prophet-'"$RUN_NAME"'.service --no-pager -o short-iso -n 20
exit $rc'
```

Add the mode to the `case "$MODE"` selector:

```bash
  run)       REMOTE="$REMOTE_RUN" ;;
```

Make the confirmation prompt honest about what `--run` costs — replace the prompt line:

```bash
  if [ "$MODE" = "run" ]; then
    printf 'Прогнати тік %s на боксі %s (%s)? Пише в прод-БД і палить LLM-гроші. [y/N] ' \
      "$RUN_NAME" "$BOX" "$IP"
  else
    printf 'Режим %s на боксі %s (%s)? [y/N] ' "$MODE" "$BOX" "$IP"
  fi
```

Add the example to the header comment (inside the `usage()` window):

```bash
#   ./deploy/timers.sh --run ingest    # прогнати один тік просто зараз (МУТУЄ ПРОД)
```

- [x] **Step 4: Run the whole suite**

```bash
.venv/bin/python -m pytest tests/test_deploy_timers.py tests/test_box_tick.py tests/test_box_units.py -q
.venv/bin/python -m pytest tests/ -q
```

Expected: the three new files pass (18 tests), and the full suite stays green — no existing test touches `deploy/timers.sh`.

- [x] **Step 5: Commit**

```bash
git add deploy/timers.sh tests/test_deploy_timers.py
git commit -m "feat(deploy): timers.sh --run — форсований тік без очікування розкладу"
```

---

### Task 6: Runbook and status docs

**Scope:** make the schedule discoverable by the operator, and close the "автоматизація/розклад інжесту" item that has been open in `progress.md` since the July prod cycle.

**Files:**
- Create: `runbook/timers.md`
- Modify: `runbook/ingest.md`, `runbook/verify.md`, `progress.md`, `docs/aws-deploy/README.md`

**Interfaces:**
- Consumes: the CLI surface from Tasks 3–5.
- Produces: no code.

- [x] **Step 1: Write `runbook/timers.md`**

Follow the shape of `runbook/verify.md`: what it is, the one command, what mutates prod, an error table, config env vars, and a dated "перевірено" footer left empty until Task 7 fills it.

Required content:
- **Що це** — two systemd timers on the box: `prophet-ingest.timer` 05:00 UTC, `prophet-verify.timer` 06:00 UTC (`?limit=50`). Source of truth `deploy/box/`, design link.
- **Команди** — the four `./deploy/timers.sh` forms from Tasks 3–5.
- **⚠️ Мутує прод** — `--run` and every scheduled tick write to the prod DB and spend LLM money.
- **Що робить тік** — flock → POST → one journald line; a busy lock means skip, not failure.
- **Коди й діагностика** — the error table from design §7.
- **Env на паузі** — no ticks; on `./deploy/start.sh` the timers resume, missed ticks are **not** replayed.
- **Після пересоздання боксу** — re-run `./deploy/timers.sh --install`; the units are not in `UserData` on purpose (design §3).

- [x] **Step 2: Cross-link the sibling runbooks**

In `runbook/ingest.md`, in the "На проді (боксі)" block, after the `./deploy/ingest.sh` lines add:

```markdown
  Щоденний автоматичний прогін — [`timers.md`](timers.md) (05:00 UTC); ця команда лишається
  ручним тригером.
```

In `runbook/verify.md`, after the "Одна команда" block add:

```markdown
Щоденний автоматичний прогін (06:00 UTC, `--limit 50`) — [`timers.md`](timers.md).
```

- [x] **Step 3: Update the status docs**

In `progress.md`:
- In the Phase 6 table, add a row: `| 25 — розклад інжесту/верифікації (systemd на боксі) | ✅ |`.
- In the Phase 6 prose, replace "Лишається 📋: автоматизація/розклад інжесту і GitHub Actions CI" with a sentence that only GitHub Actions CI remains, and point at `runbook/timers.md`.
- In the "Оновлення 2026-07-18" note, replace "Незакрите на проді: автоматизація/розклад інжесту (запускається вручну)" with a line saying the schedule landed on 2026-08-24.
- Update **Останнє оновлення** to `2026-08-24`.

In `docs/aws-deploy/README.md`, add the plan row to the document table:

```markdown
| [`2026-08-24-scheduled-ticks-plan.md`](2026-08-24-scheduled-ticks-plan.md) | Implementation plan — 7 задач |
```

- [x] **Step 4: Check the links resolve**

```bash
grep -n "timers.md" runbook/*.md progress.md
ls runbook/timers.md deploy/box/
```

Expected: `timers.md` referenced from `ingest.md` and `verify.md`, and the file exists.

- [x] **Step 5: Commit**

```bash
git add runbook/timers.md runbook/ingest.md runbook/verify.md progress.md docs/aws-deploy/README.md
git commit -m "docs(runbook): розклад тіків на боксі — timers.md + статус у progress.md"
```

---

### Task 7: Install on the live box and prove it

**Scope:** the only task that touches production. Everything before it is verifiable on the laptop; this one turns the schedule on and collects the evidence for the G3 handoff.

**Files:** none — this task produces command output, not a diff.

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: the evidence block for the handoff report, and the "перевірено" footer for `runbook/timers.md`.

**Preconditions:** the env must be up (`./deploy/status.sh` says UP, not PAUSED) — if it is paused, raise it per `runbook/stop-env.md` first. This task spends real LLM money in Step 4.

- [x] **Step 1: Pre-check the box's assumptions**

```bash
./deploy/connect.sh --box -- 'command -v flock && systemd-analyze calendar "*-*-* 05:00:00 UTC"'
```

Expected: a path for `flock`, and a calendar expansion with a `Next elapse:` line in UTC. (`--box` runs the command on the host itself, not inside the `app` container.) If `flock` is missing, stop — the design's locking assumption is broken and the plan needs revisiting, not a workaround.

- [x] **Step 2: Install**

```bash
./deploy/timers.sh --install
```

Expected: confirmation prompt, then a `list-timers` table showing `prophet-ingest.timer` and `prophet-verify.timer` with `NEXT` times at 05:00 and 06:00 UTC.

- [x] **Step 3: Verify the units are valid on the box**

```bash
./deploy/connect.sh --box -- 'systemd-analyze verify /etc/systemd/system/prophet-*.service /etc/systemd/system/prophet-*.timer'
./deploy/timers.sh
```

Expected: `systemd-analyze verify` prints nothing (silence is success), and `timers.sh` shows both timers `enabled` with no journal entries yet.

- [x] **Step 4: Force one cheap real tick**

Temporarily lower the verify cap so the first real cycle is small: edit `deploy/box/prophet-verify.service` to `?limit=5`, re-install, run it, then restore.

```bash
sed -i '' 's/limit=50/limit=5/' deploy/box/prophet-verify.service
./deploy/timers.sh --install -y
./deploy/timers.sh --run verify
```

Expected: a journal line of the form `tick=verify ok http=200 verified=N failed=0 skipped=M`. Then restore and re-install:

```bash
git checkout deploy/box/prophet-verify.service
./deploy/timers.sh --install -y
./deploy/timers.sh
```

Expected: the last line of `timers.sh` output shows the successful tick, and the units are back to `limit=50`.

- [x] **Step 5: Record the evidence**

Append a dated footer to `runbook/timers.md` in the style of `runbook/ingest.md`:

```markdown
---

_Перевірено 2026-08-24 (живий бокс):_

- _`--install` → обидва таймери enabled, NEXT 05:00 / 06:00 UTC._
- _`systemd-analyze verify` — тихо._
- _`--run verify` з `limit=5` → `tick=verify ok http=200 verified=… failed=0`._
```

Fill in the real numbers from Step 4 — do not paste the template values.

```bash
git add runbook/timers.md
git commit -m "docs(runbook): timers.md — підтверджено на живому боксі"
```

---

## Self-Review

**Spec coverage** — design §2 → Tasks 1–2; §3 (placement) → Task 2 units + Task 4 install; §3 (install path, not `UserData`) → Task 4; §4 (cadence, cap) → Task 2 constants + Task 2's tests; §5 (lock, `Persistent=false`, timeouts) → Task 1 + Task 2; §6 (`timers.sh`) → Tasks 3–5; §7 (error table) → Task 1's exit-code tests + Task 6's runbook; §8 (testing) → Tasks 1, 2, 3 test files plus Task 7's live proof; §9 (out of scope) → the Global Constraint that `src/` must not change.

**Type consistency** — the tick signature `prophet-tick.sh <name> <url> <timeout>` is defined in Task 1 and used verbatim in Task 2's `ExecStart=` lines and Task 2's tests. Unit names `prophet-{ingest,verify}.{service,timer}` are fixed in Task 2 and used verbatim in Tasks 3, 4, 5, 6, 7. `BOX_DIR` defaults to `deploy/box` in Task 3 and is consumed by Task 4's install.

**Known sharp edges, called out where they bite** — the one-line JSON body forces `grep -o | wc -l` over `grep -c` in Task 1; the `usage()` `sed` window widening in Task 4 Step 4; `--run -y` swallowing the flag as a name in Task 5 Step 1.
