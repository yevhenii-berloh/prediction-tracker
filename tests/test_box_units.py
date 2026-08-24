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
        "ExecStart=/usr/local/bin/prophet-tick.sh ingest http://localhost:8000/ingest/run 3600"
        in unit("prophet-ingest.service")
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
            ln for ln in unit(f"{name}.service").splitlines() if ln.startswith("TimeoutStartSec=")
        ]
        assert len(line) == 1, name
        assert int(line[0].split("=", 1)[1]) > curl_timeout, name
