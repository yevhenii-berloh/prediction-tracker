from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from prophet_checker.ingestion import IngestionOrchestrator
from prophet_checker.models.domain import (
    ExtractionOutcome,
    PersonSource,
    Prediction,
    PredictionStatus,
    RawDocument,
    SourceType,
)
from prophet_checker.sources.mock import MockSource
from fakes import FakeSourceRepo, FakePredictionRepo


def _stub_session_factory():
    factory = MagicMock(spec=async_sessionmaker)
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=tx_ctx)
    tx_ctx.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=tx_ctx)
    factory.return_value = session
    return factory


async def test_end_to_end_three_posts_with_mocked_llm():
    person_source = PersonSource(
        id="ps1",
        person_id="p1",
        source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    docs = [
        RawDocument(
            id=f"tg:arestovich:{i}",
            person_id="p1",
            source_type=SourceType.TELEGRAM,
            url=f"https://t.me/arestovich/{i}",
            published_at=datetime(2024, 1, 2 + i, tzinfo=UTC),
            raw_text=f"Post {i}",
        )
        for i in range(3)
    ]
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(person_source)
    prediction_repo = FakePredictionRepo()

    p1 = Prediction(
        id="pred-a", document_id="x", person_id="p1",
        claim_text="A", prediction_date=date(2024, 1, 1),
    )
    p2 = Prediction(
        id="pred-b", document_id="x", person_id="p1",
        claim_text="B", prediction_date=date(2024, 1, 1),
    )
    p3 = Prediction(
        id="pred-c", document_id="x", person_id="p1",
        claim_text="C", prediction_date=date(2024, 1, 1),
    )

    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=[ExtractionOutcome(predictions=[p1, p2]), ExtractionOutcome(), ExtractionOutcome(predictions=[p3])])
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 1536)

    orchestrator = IngestionOrchestrator(
        session_factory=_stub_session_factory(),
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=extractor,
        embedder=embedder,
        sources={SourceType.TELEGRAM: MockSource(docs)},
    )

    report = await orchestrator.run_cycle()

    ch = report.channels_processed[0]
    assert ch.posts_seen == 3
    assert ch.predictions_extracted == 3
    assert len(prediction_repo._predictions) == 3
    saved_ids = {p.id for p in prediction_repo._predictions}
    assert saved_ids == {"pred-a", "pred-b", "pred-c"}
    updated = await source_repo.get_person_sources("p1")
    assert updated[0].last_collected_at == datetime(2024, 1, 4, tzinfo=UTC)


async def test_halt_recovery_resumes_from_last_cursor():
    person_source = PersonSource(
        id="ps1",
        person_id="p1",
        source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    docs = [
        RawDocument(
            id=f"tg:arestovich:{i}",
            person_id="p1",
            source_type=SourceType.TELEGRAM,
            url=f"https://t.me/arestovich/{i}",
            published_at=datetime(2024, 1, 2 + i, tzinfo=UTC),
            raw_text=f"Post {i}",
        )
        for i in range(3)
    ]
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(person_source)
    prediction_repo = FakePredictionRepo()

    pred = Prediction(
        id="pred-x", document_id="x", person_id="p1",
        claim_text="X", prediction_date=date(2024, 1, 1),
    )

    cycle1_extract = MagicMock()
    cycle1_extract.extract = AsyncMock(side_effect=[ExtractionOutcome(predictions=[pred]), RuntimeError("LLM down")])
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 1536)

    orch1 = IngestionOrchestrator(
        session_factory=_stub_session_factory(),
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=cycle1_extract,
        embedder=embedder,
        sources={SourceType.TELEGRAM: MockSource(docs)},
    )
    report1 = await orch1.run_cycle()
    assert report1.channels_processed[0].error is not None
    assert len(prediction_repo._predictions) == 1

    updated = await source_repo.get_person_sources("p1")
    assert updated[0].last_collected_at == datetime(2024, 1, 2, tzinfo=UTC)

    cycle2_extract = MagicMock()
    cycle2_extract.extract = AsyncMock(side_effect=[ExtractionOutcome(predictions=[pred]), ExtractionOutcome(predictions=[pred])])

    orch2 = IngestionOrchestrator(
        session_factory=_stub_session_factory(),
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=cycle2_extract,
        embedder=embedder,
        sources={SourceType.TELEGRAM: MockSource(docs)},
    )
    report2 = await orch2.run_cycle()
    ch2 = report2.channels_processed[0]
    assert ch2.error is None
    assert ch2.posts_seen == 2
    assert ch2.predictions_extracted == 2
    assert len(prediction_repo._predictions) == 3
    final = await source_repo.get_person_sources("p1")
    assert final[0].last_collected_at == datetime(2024, 1, 4, tzinfo=UTC)
