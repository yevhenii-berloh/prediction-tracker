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
