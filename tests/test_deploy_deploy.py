"""Tests for deploy/deploy.sh — a deploy now also refreshes .env from S3 so the
container comes up on current secrets, not the stale bootstrap copy.

Hermetic: `--dry-run` prints the remote block and exits before any AWS/SSH call,
so we assert the secrets-pull step is present alongside the existing code steps.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY_SH = REPO / "deploy" / "deploy.sh"


def dry_run(*args):
    return subprocess.run(
        ["bash", str(DEPLOY_SH), "--dry-run", *args],
        capture_output=True,
        text=True,
    )


def test_dry_run_refreshes_secrets_from_s3():
    proc = dry_run()
    assert proc.returncode == 0, proc.stderr
    assert 'aws s3 cp "s3://$BUCKET/.env" /opt/app/.env' in proc.stdout


def test_dry_run_forces_recreate_so_new_env_is_read():
    proc = dry_run()
    assert proc.returncode == 0, proc.stderr
    assert "--force-recreate" in proc.stdout


def test_dry_run_still_pulls_code_and_builds():
    proc = dry_run()
    assert proc.returncode == 0, proc.stderr
    assert "git pull --ff-only" in proc.stdout
    assert "up -d --build" in proc.stdout
