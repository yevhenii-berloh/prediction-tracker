"""Tests for deploy/verify.sh — resolve the box and trigger one verification cycle
via POST /verify/run over SSH.

Hermetic: fake `aws` and `ssh` on PATH. The fake `ssh` records its full argv (which
includes the remote `curl ... /verify/run` command) so we assert what *would* run on
the box, and echoes a canned report + `__HTTP__ 200` marker so verify.sh's response
parser has something to read (without a real connection).
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VERIFY_SH = REPO / "deploy" / "verify.sh"

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
# fake ssh — record argv (incl. the remote command), then echo a canned verify
# report and the HTTP marker so verify.sh parses a 200.
[ -n "${FAKE_SSH_LOG:-}" ] && printf '%s\n' "$@" >> "$FAKE_SSH_LOG"
printf '%s\n' '{"verified":3,"failed":0,"skipped":1,"entries":[]}'
printf '__HTTP__ 200\n'
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


def run_verify(env, *args, aws_scenario="running"):
    e = dict(env["env"])
    e["FAKE_AWS_SCENARIO"] = aws_scenario
    return subprocess.run(
        ["bash", str(VERIFY_SH), *args],
        env=e,
        capture_output=True,
        text=True,
    )


def _remote(env) -> str:
    return env["ssh_log"].read_text() if env["ssh_log"].exists() else ""


def test_default_posts_verify_run(env):
    proc = run_verify(env, "-y")
    assert proc.returncode == 0, proc.stderr
    remote = _remote(env)
    assert "-X POST localhost:8000/verify/run" in remote
    assert "limit=" not in remote  # no --limit → no query string


def test_limit_flag_appends_query(env):
    proc = run_verify(env, "-y", "--limit", "5")
    assert proc.returncode == 0, proc.stderr
    assert "/verify/run?limit=5" in _remote(env)


def test_happy_path_prints_done(env):
    proc = run_verify(env, "-y")
    assert proc.returncode == 0, proc.stderr
    assert "завершено" in proc.stdout


def test_dry_run_prints_plan_without_ssh(env):
    proc = run_verify(env, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "/verify/run" in proc.stdout
    assert not env["ssh_log"].exists(), "dry-run must not SSH"


def test_no_running_box_dies_before_ssh(env):
    proc = run_verify(env, "-y", aws_scenario="nobox")
    assert proc.returncode != 0
    assert "stop-env" in proc.stderr
    assert not env["ssh_log"].exists(), "must not SSH when there is no live box"


def test_help_does_not_touch_aws_or_ssh(env):
    proc = run_verify(env, "--help")
    assert proc.returncode == 0
    assert "verify.sh" in proc.stdout
    assert not env["ssh_log"].exists()
