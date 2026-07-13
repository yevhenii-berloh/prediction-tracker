"""Tests for deploy/refresh.sh — pull the fresh .env from S3 onto the live box
and recreate the app so new secrets take effect.

Hermetic: fake `aws` and `ssh` on PATH. `--dry-run` prints the remote block to
stdout (no AWS/SSH), so we assert what *would* run on the box; the live path is
exercised with the fakes to check bucket + box resolution and the SSH argv.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
REFRESH_SH = REPO / "deploy" / "refresh.sh"

FAKE_AWS = r"""#!/usr/bin/env bash
# fake aws — cloudformation resolves the secrets bucket; ec2 resolves id then IP
svc="$1"
scen="${FAKE_AWS_SCENARIO:-running}"
case "$svc" in
  cloudformation) echo "prophet-secrets-bucket-xyz" ;;
  ec2)
    if printf '%s ' "$@" | grep -q -- '--instance-ids'; then
      echo "63.185.91.181"
    else
      [ "$scen" = "nobox" ] || echo "i-0b2811b6"
    fi ;;
esac
exit 0
"""

FAKE_SSH = r"""#!/usr/bin/env bash
# fake ssh — record argv (incl. the bucket arg) then drain the piped remote block
[ -n "${FAKE_SSH_LOG:-}" ] && printf '%s\n' "$@" >> "$FAKE_SSH_LOG"
cat >/dev/null 2>&1 || true
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


def run_refresh(env, *args, aws_scenario="running", extra_env=None):
    e = dict(env["env"])
    e["FAKE_AWS_SCENARIO"] = aws_scenario
    if extra_env:
        e.update(extra_env)
    return subprocess.run(
        ["bash", str(REFRESH_SH), *args],
        env=e,
        capture_output=True,
        text=True,
    )


def _remote(env) -> str:
    return env["ssh_log"].read_text() if env["ssh_log"].exists() else ""


def test_dry_run_pulls_env_then_recreates(env):
    proc = run_refresh(env, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert 'aws s3 cp "s3://$BUCKET/.env" /opt/app/.env' in out
    assert "up -d --force-recreate" in out


def test_dry_run_touches_no_ssh(env):
    proc = run_refresh(env, "--dry-run")
    assert proc.returncode == 0
    assert not env["ssh_log"].exists(), "dry-run must not SSH"


def test_live_run_passes_resolved_bucket_to_box(env):
    proc = run_refresh(env, "-y")
    assert proc.returncode == 0, proc.stderr
    assert "prophet-secrets-bucket-xyz" in _remote(env)


def test_secrets_bucket_override_is_used(env):
    proc = run_refresh(env, "-y", extra_env={"SECRETS_BUCKET": "my-override-bucket"})
    assert proc.returncode == 0, proc.stderr
    assert "my-override-bucket" in _remote(env)


def test_no_running_box_dies_before_ssh(env):
    proc = run_refresh(env, "-y", aws_scenario="nobox")
    assert proc.returncode != 0
    assert "stop-env" in proc.stderr
    assert not env["ssh_log"].exists(), "must not SSH when there is no live box"


def test_help_does_not_touch_aws_or_ssh(env):
    proc = run_refresh(env, "--help")
    assert proc.returncode == 0
    assert "refresh.sh" in proc.stdout
    assert not env["ssh_log"].exists()
