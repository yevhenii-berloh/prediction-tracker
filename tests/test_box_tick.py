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
    return subprocess.run(["bash", str(TICK_SH), *args], env=e, capture_output=True, text=True)


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
