from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from prophet_checker.app import app
from prophet_checker.ingestion import ChannelReport, CycleReport


@pytest.fixture(autouse=True)
def _clear_orchestrator_state():
    yield
    if hasattr(app.state, "orchestrator"):
        delattr(app.state, "orchestrator")


async def test_health_returns_ok():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ingest_run_returns_cycle_report():
    orchestrator = MagicMock()
    orchestrator.run_cycle = AsyncMock(
        return_value=CycleReport(
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            channels_processed=[
                ChannelReport(
                    person_source_id="ps1",
                    posts_seen=3,
                    posts_with_predictions=2,
                    predictions_extracted=5,
                ),
            ],
        )
    )
    app.state.orchestrator = orchestrator

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/ingest/run")

    assert resp.status_code == 200
    body = resp.json()
    assert "channels_processed" in body
    assert "started_at" in body
    assert "finished_at" in body
    assert len(body["channels_processed"]) == 1
    assert body["channels_processed"][0]["person_source_id"] == "ps1"
    assert body["channels_processed"][0]["predictions_extracted"] == 5


async def test_ingest_run_503_when_orchestrator_not_initialized():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/ingest/run")

    assert resp.status_code == 503
    assert "orchestrator not initialized" in resp.json()["detail"]


async def test_ingest_run_500_on_catastrophic_exception():
    orchestrator = MagicMock()
    orchestrator.run_cycle = AsyncMock(side_effect=RuntimeError("boom"))
    app.state.orchestrator = orchestrator

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/ingest/run")

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "RuntimeError" in detail
    assert "boom" not in detail


async def test_ingest_run_returns_per_channel_errors_as_200():
    orchestrator = MagicMock()
    orchestrator.run_cycle = AsyncMock(
        return_value=CycleReport(
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            channels_processed=[
                ChannelReport(
                    person_source_id="ps1",
                    posts_seen=2,
                    error="halted at step=processing: LLM 503",
                ),
                ChannelReport(
                    person_source_id="ps2",
                    posts_seen=5,
                    predictions_extracted=3,
                ),
            ],
        )
    )
    app.state.orchestrator = orchestrator

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/ingest/run")

    assert resp.status_code == 200
    body = resp.json()
    channels = body["channels_processed"]
    assert len(channels) == 2
    assert channels[0]["error"] is not None
    assert "halted" in channels[0]["error"]
    assert channels[1]["error"] is None


@pytest.fixture(autouse=True)
def _clear_query_orchestrator_state():
    yield
    if hasattr(app.state, "query_orchestrator"):
        delattr(app.state, "query_orchestrator")


async def test_query_returns_results():
    from prophet_checker.models.domain import Prediction, QueryResult, RetrievedPrediction

    qo = MagicMock()
    pred = Prediction(
        id="p1", document_id="d", person_id="x", claim_text="c", prediction_date=date(2024, 1, 1)
    )
    qo.search = AsyncMock(
        return_value=QueryResult(
            query="q", results=[RetrievedPrediction(prediction=pred, distance=0.1, rank=1)]
        )
    )
    app.state.query_orchestrator = qo

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/query", json={"question": "q", "limit": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "q"
    assert body["results"][0]["prediction"]["id"] == "p1"
    assert body["results"][0]["rank"] == 1


async def test_query_422_on_empty_question():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/query", json={"question": "", "limit": 5})
    assert resp.status_code == 422


async def test_query_503_when_not_initialized():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/query", json={"question": "q"})
    assert resp.status_code == 503


@pytest.fixture(autouse=True)
def _clear_answer_orchestrator_state():
    yield
    if hasattr(app.state, "answer_orchestrator"):
        delattr(app.state, "answer_orchestrator")


async def test_answer_returns_answer_and_sources():
    from prophet_checker.models.domain import (
        AnswerResult,
        Prediction,
        RetrievedPrediction,
    )

    ao = MagicMock()
    pred = Prediction(
        id="p1", document_id="d", person_id="x", claim_text="c", prediction_date=date(2024, 1, 1)
    )
    ao.answer = AsyncMock(
        return_value=AnswerResult(
            query="q",
            answer="відповідь",
            sources=[RetrievedPrediction(prediction=pred, distance=0.1, rank=1)],
        )
    )
    app.state.answer_orchestrator = ao

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/answer", json={"question": "q", "limit": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "відповідь"
    assert body["sources"][0]["prediction"]["id"] == "p1"


async def test_answer_422_on_empty_question():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/answer", json={"question": "", "limit": 5})
    assert resp.status_code == 422


async def test_answer_503_when_not_initialized():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/answer", json={"question": "q"})
    assert resp.status_code == 503


@pytest.fixture(autouse=True)
def _clear_verification_orchestrator_state():
    yield
    if hasattr(app.state, "verification_orchestrator"):
        delattr(app.state, "verification_orchestrator")


def _verification_report(**kwargs):
    from prophet_checker.verification.report import VerificationCycleReport

    defaults = dict(started_at=datetime.now(UTC), finished_at=datetime.now(UTC))
    return VerificationCycleReport(**{**defaults, **kwargs})


async def test_verify_run_returns_report():
    from prophet_checker.verification.report import VerificationEntry

    orchestrator = MagicMock()
    orchestrator.run_cycle = AsyncMock(
        return_value=_verification_report(
            verified=2,
            failed=1,
            skipped=3,
            entries=[
                VerificationEntry(prediction_id="p1", status="confirmed"),
                VerificationEntry(prediction_id="p2", error="RuntimeError: boom"),
            ],
        )
    )
    app.state.verification_orchestrator = orchestrator

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/verify/run")

    assert resp.status_code == 200
    body = resp.json()
    assert body["verified"] == 2
    assert body["failed"] == 1
    assert body["skipped"] == 3
    assert len(body["entries"]) == 2
    assert body["entries"][0]["prediction_id"] == "p1"
    assert body["entries"][0]["status"] == "confirmed"
    orchestrator.run_cycle.assert_awaited_once_with(limit=None)


async def test_verify_run_passes_limit():
    orchestrator = MagicMock()
    orchestrator.run_cycle = AsyncMock(return_value=_verification_report(verified=5))
    app.state.verification_orchestrator = orchestrator

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/verify/run?limit=5")

    assert resp.status_code == 200
    orchestrator.run_cycle.assert_awaited_once_with(limit=5)


async def test_verify_run_503_when_orchestrator_not_initialized():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/verify/run")

    assert resp.status_code == 503
    assert "verification orchestrator not initialized" in resp.json()["detail"]


async def test_verify_run_500_on_catastrophic_exception():
    orchestrator = MagicMock()
    orchestrator.run_cycle = AsyncMock(side_effect=RuntimeError("boom"))
    app.state.verification_orchestrator = orchestrator

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/verify/run")

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "RuntimeError" in detail
    assert "boom" not in detail


async def test_verify_run_422_on_limit_below_one():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/verify/run?limit=0")
    assert resp.status_code == 422
