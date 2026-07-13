"""Tests for deploy/logs.sh — resolve the box and stream its container logs over SSH.

Hermetic: fake `aws` and `ssh` on PATH. The fake `ssh` records its full argv
(which includes the remote `docker compose logs ...` command) to a file, so we
assert what *would* run on the box without a real connection or a live stream.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LOGS_SH = REPO / "deploy" / "logs.sh"

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
# fake ssh — record argv (incl. the remote command) so tests can inspect it
[ -n "${FAKE_SSH_LOG:-}" ] && printf '%s\n' "$@" >> "$FAKE_SSH_LOG"
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
    return {"env": base, "ssh_log": ssh_log}


def run_logs(env, *args, aws_scenario="running"):
    e = dict(env["env"])
    e["FAKE_AWS_SCENARIO"] = aws_scenario
    return subprocess.run(
        ["bash", str(LOGS_SH), *args],
        env=e,
        capture_output=True,
        text=True,
    )


def _remote(env) -> str:
    return env["ssh_log"].read_text() if env["ssh_log"].exists() else ""


def test_default_tails_app_logs(env):
    proc = run_logs(env)
    assert proc.returncode == 0, proc.stderr
    assert "docker compose -f docker-compose.yml logs --tail=100 app" in _remote(env)


def test_follow_flag_streams_with_tty(env):
    proc = run_logs(env, "-f")
    assert proc.returncode == 0, proc.stderr
    remote = _remote(env)
    assert "-f app" in remote
    assert "-tt" in remote  # follow allocates a tty so Ctrl-C works


def test_migrate_flag_targets_migrate_service(env):
    proc = run_logs(env, "--migrate")
    assert proc.returncode == 0, proc.stderr
    assert "logs --tail=100 migrate" in _remote(env)


def test_tail_flag_overrides_count(env):
    proc = run_logs(env, "--tail", "50")
    assert proc.returncode == 0, proc.stderr
    assert "--tail=50" in _remote(env)


def test_since_flag_is_passed_through(env):
    proc = run_logs(env, "--since", "30m")
    assert proc.returncode == 0, proc.stderr
    assert "--since 30m" in _remote(env)


def test_no_running_box_dies_before_ssh(env):
    proc = run_logs(env, aws_scenario="nobox")
    assert proc.returncode != 0
    assert "stop-env" in proc.stderr
    assert not env["ssh_log"].exists(), "must not SSH when there is no live box"


def test_help_does_not_touch_aws_or_ssh(env):
    proc = run_logs(env, "--help")
    assert proc.returncode == 0
    assert "logs.sh" in proc.stdout
    assert not env["ssh_log"].exists()
