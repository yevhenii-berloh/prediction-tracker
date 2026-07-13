"""Tests for deploy/status.sh — read-only AWS status reporter.

Hermetic: fake `aws` and `ssh` executables are placed on PATH so no real
AWS calls or SSH connections happen. Each fake emits canned output driven by
FAKE_AWS_SCENARIO / FAKE_SSH_SCENARIO, letting us drive status.sh through the
env states it must handle (up / paused / not-deployed / ssh-unreachable /
no-auth) and assert its verdict + exit code.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STATUS_SH = REPO / "deploy" / "status.sh"

FAKE_AWS = r"""#!/usr/bin/env bash
# fake aws CLI for status.sh tests — canned output per FAKE_AWS_SCENARIO
svc="$1"
scen="${FAKE_AWS_SCENARIO:-up}"
case "$svc" in
  sts)
    [ "$scen" = "noauth" ] && { echo "Unable to locate credentials" >&2; exit 255; }
    echo "043174661707" ;;
  cloudformation)
    [ "$scen" = "absent" ] && { echo "Stack does not exist" >&2; exit 255; }
    echo "CREATE_COMPLETE" ;;
  ec2)
    case "$scen" in
      absent) : ;;
      paused) printf 'i-0b2811b6\tstopped\tNone\n' ;;
      *)      printf 'i-0b2811b6\trunning\t1.2.3.4\n' ;;
    esac ;;
  rds)
    case "$scen" in
      absent) : ;;
      paused) printf 'prophet-data-uq42\tstopped\n' ;;
      *)      printf 'prophet-data-uq42\tavailable\n' ;;
    esac ;;
esac
exit 0
"""

FAKE_SSH = r"""#!/usr/bin/env bash
# fake ssh for status.sh tests — records invocation, emits canned remote output
[ -n "${FAKE_SSH_LOG:-}" ] && echo "invoked" >> "$FAKE_SSH_LOG"
cat >/dev/null 2>&1 || true   # drain the piped REMOTE block so printf doesn't SIGPIPE
scen="${FAKE_SSH_SCENARIO:-ok}"
case "$scen" in
  unreachable) echo "ssh: connect to host: Operation timed out" >&2; exit 255 ;;
  *) echo "HEALTH=200"; echo "MIGRATE=0"; echo "APPUP=1" ;;
esac
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
    return {"env": base, "ssh_log": ssh_log, "key": key}


def run_status(env, *args, aws_scenario="up", ssh_scenario="ok"):
    e = dict(env["env"])
    e["FAKE_AWS_SCENARIO"] = aws_scenario
    e["FAKE_SSH_SCENARIO"] = ssh_scenario
    return subprocess.run(
        ["bash", str(STATUS_SH), *args],
        env=e,
        capture_output=True,
        text=True,
    )


def test_up_reports_healthy(env):
    proc = run_status(env, aws_scenario="up", ssh_scenario="ok")
    assert proc.returncode == 0, proc.stderr
    assert "UP (healthy)" in proc.stdout
    assert "200" in proc.stdout
    assert env["ssh_log"].exists(), "expected the app layer to SSH into the box"


def test_paused_skips_ssh(env):
    proc = run_status(env, aws_scenario="paused")
    assert proc.returncode == 0, proc.stderr
    assert "PAUSED" in proc.stdout
    assert "box stopped" in proc.stdout
    assert not env["ssh_log"].exists(), "must not SSH into a stopped box"


def test_not_deployed(env):
    proc = run_status(env, aws_scenario="absent")
    assert proc.returncode == 0, proc.stderr
    assert "NOT DEPLOYED" in proc.stdout
    assert "ABSENT" in proc.stdout
    assert not env["ssh_log"].exists()


def test_ssh_unreachable_is_partial(env):
    proc = run_status(env, aws_scenario="up", ssh_scenario="unreachable")
    assert proc.returncode == 0, proc.stderr
    assert "PARTIAL" in proc.stdout
    assert "SSH unreachable" in proc.stdout
    assert env["ssh_log"].exists(), "unreachable still means SSH was attempted"


def test_preflight_fails_without_credentials(env):
    proc = run_status(env, aws_scenario="noauth")
    assert proc.returncode != 0
    assert "aws" in proc.stderr.lower()
    assert "Verdict" not in proc.stdout


def test_no_ssh_flag_skips_app_layer(env):
    proc = run_status(env, "--no-ssh", aws_scenario="up")
    assert proc.returncode == 0, proc.stderr
    assert "unchecked" in proc.stdout
    assert not env["ssh_log"].exists(), "--no-ssh must not invoke ssh"
