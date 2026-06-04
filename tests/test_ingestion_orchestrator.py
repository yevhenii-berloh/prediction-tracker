from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from prophet_checker.ingestion import ChannelReport, CycleReport
from prophet_checker.ingestion.orchestrator import IngestionOrchestrator
from prophet_checker.models.domain import (
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
    return factory, session


def _make_extractor(predictions: list[Prediction]):
    extractor = MagicMock()
    extractor.extract = AsyncMock(return_value=predictions)
    return extractor


def _make_embedder(vector: list[float] | None = None):
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=vector or [0.1] * 1536)
    return embedder


async def test_run_cycle_no_active_sources():
    factory, _ = _stub_session_factory()
    orchestrator = IngestionOrchestrator(
        session_factory=factory,
        source_repo=FakeSourceRepo(),
        prediction_repo=FakePredictionRepo(),
        extractor=_make_extractor([]),
        embedder=_make_embedder(),
        sources={},
    )

    report = await orchestrator.run_cycle()

    assert isinstance(report, CycleReport)
    assert report.channels_processed == []


async def test_run_cycle_processes_posts_in_one_channel():
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
    extractor = MagicMock()
    pred = Prediction(
        id="pred-1",
        document_id="x",
        person_id="p1",
        claim_text="claim",
        prediction_date=date(2024, 1, 1),
    )
    extractor.extract = AsyncMock(side_effect=[[pred], [], [pred, pred]])
    embedder = _make_embedder()
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory,
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=extractor,
        embedder=embedder,
        sources={SourceType.TELEGRAM: MockSource(docs)},
    )

    report = await orchestrator.run_cycle()

    assert len(report.channels_processed) == 1
    ch = report.channels_processed[0]
    assert ch.person_source_id == "ps1"
    assert ch.posts_seen == 3
    assert ch.posts_with_predictions == 2
    assert ch.predictions_extracted == 3
    assert extractor.extract.call_count == 3
    assert embedder.embed.call_count == 3
    assert len(prediction_repo._predictions) == 3


async def test_empty_predictions_advances_cursor_without_save():
    person_source = PersonSource(
        id="ps1",
        person_id="p1",
        source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    doc = RawDocument(
        id="tg:arestovich:1",
        person_id="p1",
        source_type=SourceType.TELEGRAM,
        url="https://t.me/arestovich/1",
        published_at=datetime(2024, 1, 5, tzinfo=UTC),
        raw_text="No predictions here",
    )
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(person_source)
    prediction_repo = FakePredictionRepo()
    extractor = _make_extractor([])
    embedder = _make_embedder()
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory,
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=extractor,
        embedder=embedder,
        sources={SourceType.TELEGRAM: MockSource([doc])},
    )

    report = await orchestrator.run_cycle()

    ch = report.channels_processed[0]
    assert ch.posts_seen == 1
    assert ch.posts_with_predictions == 0
    assert ch.predictions_extracted == 0
    assert len(prediction_repo._predictions) == 0
    assert embedder.embed.call_count == 0
    updated = await source_repo.get_person_sources("p1")
    assert updated[0].last_collected_at == datetime(2024, 1, 5, tzinfo=UTC)


async def test_embed_failure_halts_channel_no_save():
    person_source = PersonSource(
        id="ps1",
        person_id="p1",
        source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    doc = RawDocument(
        id="tg:arestovich:1",
        person_id="p1",
        source_type=SourceType.TELEGRAM,
        url="https://t.me/arestovich/1",
        published_at=datetime(2024, 1, 5, tzinfo=UTC),
        raw_text="Has predictions",
    )
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(person_source)
    prediction_repo = FakePredictionRepo()
    pred = Prediction(
        id="pred-1",
        document_id="x",
        person_id="p1",
        claim_text="claim",
        prediction_date=date(2024, 1, 1),
    )
    extractor = _make_extractor([pred])
    embedder = MagicMock()
    embedder.embed = AsyncMock(side_effect=RuntimeError("embed API down"))
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory,
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=extractor,
        embedder=embedder,
        sources={SourceType.TELEGRAM: MockSource([doc])},
    )

    report = await orchestrator.run_cycle()

    ch = report.channels_processed[0]
    assert ch.error is not None
    assert "halted at step=processing" in ch.error
    assert "embed API down" in ch.error
    assert len(prediction_repo._predictions) == 0
    updated = await source_repo.get_person_sources("p1")
    assert updated[0].last_collected_at == datetime(2024, 1, 1, tzinfo=UTC)


async def test_save_failure_halts_channel():
    person_source = PersonSource(
        id="ps1",
        person_id="p1",
        source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    doc = RawDocument(
        id="tg:arestovich:1",
        person_id="p1",
        source_type=SourceType.TELEGRAM,
        url="https://t.me/arestovich/1",
        published_at=datetime(2024, 1, 5, tzinfo=UTC),
        raw_text="Has predictions",
    )
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(person_source)
    prediction_repo = FakePredictionRepo()
    prediction_repo.save = AsyncMock(side_effect=RuntimeError("DB unavailable"))
    pred = Prediction(
        id="pred-1",
        document_id="x",
        person_id="p1",
        claim_text="claim",
        prediction_date=date(2024, 1, 1),
    )
    extractor = _make_extractor([pred])
    embedder = _make_embedder()
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory,
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=extractor,
        embedder=embedder,
        sources={SourceType.TELEGRAM: MockSource([doc])},
    )

    report = await orchestrator.run_cycle()

    ch = report.channels_processed[0]
    assert ch.error is not None
    updated = await source_repo.get_person_sources("p1")
    assert updated[0].last_collected_at == datetime(2024, 1, 1, tzinfo=UTC)


async def test_one_channel_halt_does_not_block_others():
    ps1 = PersonSource(
        id="ps1",
        person_id="p1",
        source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    ps2 = PersonSource(
        id="ps2",
        person_id="p2",
        source_type=SourceType.TELEGRAM,
        source_identifier="@podolyak",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    docs = [
        RawDocument(
            id="tg:arestovich:1",
            person_id="p1",
            source_type=SourceType.TELEGRAM,
            url="https://t.me/arestovich/1",
            published_at=datetime(2024, 1, 5, tzinfo=UTC),
            raw_text="Bad post",
        ),
        RawDocument(
            id="tg:podolyak:1",
            person_id="p2",
            source_type=SourceType.TELEGRAM,
            url="https://t.me/podolyak/1",
            published_at=datetime(2024, 1, 5, tzinfo=UTC),
            raw_text="Good post",
        ),
    ]
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(ps1)
    await source_repo.save_person_source(ps2)
    prediction_repo = FakePredictionRepo()

    pred = Prediction(
        id="pred-1",
        document_id="x",
        person_id="p2",
        claim_text="claim",
        prediction_date=date(2024, 1, 1),
    )
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=[RuntimeError("LLM down"), [pred]])
    embedder = _make_embedder()
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory,
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=extractor,
        embedder=embedder,
        sources={SourceType.TELEGRAM: MockSource(docs)},
    )

    report = await orchestrator.run_cycle()

    assert len(report.channels_processed) == 2
    by_id = {c.person_source_id: c for c in report.channels_processed}
    assert by_id["ps1"].error is not None
    assert by_id["ps2"].error is None
    assert by_id["ps2"].predictions_extracted == 1


async def test_cursor_advances_per_post():
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
    cursor_calls = []
    original_update = source_repo.update_source_cursor

    async def tracking_update(person_source_id, cursor, session=None):
        cursor_calls.append((person_source_id, cursor))
        return await original_update(person_source_id, cursor, session=session)

    source_repo.update_source_cursor = tracking_update

    extractor = _make_extractor([])
    embedder = _make_embedder()
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory,
        source_repo=source_repo,
        prediction_repo=FakePredictionRepo(),
        extractor=extractor,
        embedder=embedder,
        sources={SourceType.TELEGRAM: MockSource(docs)},
    )

    await orchestrator.run_cycle()

    assert len(cursor_calls) == 3
    assert cursor_calls[0] == ("ps1", datetime(2024, 1, 2, tzinfo=UTC))
    assert cursor_calls[1] == ("ps1", datetime(2024, 1, 3, tzinfo=UTC))
    assert cursor_calls[2] == ("ps1", datetime(2024, 1, 4, tzinfo=UTC))


async def test_cycle_report_aggregates_counts():
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
        id="pred-1",
        document_id="x",
        person_id="p1",
        claim_text="claim",
        prediction_date=date(2024, 1, 1),
    )
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=[[pred, pred], [], [pred]])
    embedder = _make_embedder()
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory,
        source_repo=source_repo,
        prediction_repo=prediction_repo,
        extractor=extractor,
        embedder=embedder,
        sources={SourceType.TELEGRAM: MockSource(docs)},
    )

    report = await orchestrator.run_cycle()

    ch = report.channels_processed[0]
    assert ch.posts_seen == 3
    assert ch.posts_with_predictions == 2
    assert ch.predictions_extracted == 3
    assert report.started_at <= report.finished_at


async def test_unregistered_source_type_marks_error_and_continues():
    ps_news = PersonSource(
        id="ps_news",
        person_id="p1",
        source_type=SourceType.NEWS,
        source_identifier="some-news-feed",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(ps_news)
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory,
        source_repo=source_repo,
        prediction_repo=FakePredictionRepo(),
        extractor=_make_extractor([]),
        embedder=_make_embedder(),
        sources={SourceType.TELEGRAM: MockSource([])},
    )

    report = await orchestrator.run_cycle()

    assert len(report.channels_processed) == 1
    ch = report.channels_processed[0]
    assert ch.error is not None
    assert "NEWS" in ch.error.upper() or "news" in ch.error


async def test_run_cycle_persists_raw_documents():
    person_source = PersonSource(
        id="ps1", person_id="p1", source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    docs = [
        RawDocument(
            id=f"tg:arestovich:{i}", person_id="p1", source_type=SourceType.TELEGRAM,
            url=f"https://t.me/arestovich/{i}",
            published_at=datetime(2024, 1, 2 + i, tzinfo=UTC), raw_text=f"Post {i}",
        )
        for i in range(2)
    ]
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(person_source)
    prediction_repo = FakePredictionRepo()
    pred = Prediction(
        id="pred-1", document_id="tg:arestovich:0", person_id="p1",
        claim_text="claim", prediction_date=date(2024, 1, 1),
    )
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=[[pred], []])
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory, source_repo=source_repo,
        prediction_repo=prediction_repo, extractor=extractor,
        embedder=_make_embedder(), sources={SourceType.TELEGRAM: MockSource(docs)},
    )

    await orchestrator.run_cycle()

    saved = {d.id for d in source_repo._documents}
    assert "tg:arestovich:0" in saved
    assert "tg:arestovich:1" not in saved


async def test_mock_source_collect_honors_limit():
    person_source = PersonSource(
        id="ps1", person_id="p1", source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    docs = [
        RawDocument(
            id=f"d{i}", person_id="p1", source_type=SourceType.TELEGRAM,
            url=f"u{i}", published_at=datetime(2024, 1, 2 + i, tzinfo=UTC), raw_text="t",
        )
        for i in range(5)
    ]
    collected = [d async for d in MockSource(docs).collect(person_source, limit=2)]
    assert len(collected) == 2


async def test_run_cycle_passes_limit_to_collect():
    person_source = PersonSource(
        id="ps1", person_id="p1", source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    docs = [
        RawDocument(
            id=f"d{i}", person_id="p1", source_type=SourceType.TELEGRAM,
            url=f"u{i}", published_at=datetime(2024, 1, 2 + i, tzinfo=UTC), raw_text="t",
        )
        for i in range(5)
    ]
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(person_source)
    factory, _ = _stub_session_factory()
    orchestrator = IngestionOrchestrator(
        session_factory=factory, source_repo=source_repo,
        prediction_repo=FakePredictionRepo(), extractor=_make_extractor([]),
        embedder=_make_embedder(), sources={SourceType.TELEGRAM: MockSource(docs)},
    )

    report = await orchestrator.run_cycle(limit=1)

    assert report.channels_processed[0].posts_seen == 1


async def test_run_cycle_skips_embedding_when_no_embedder():
    person_source = PersonSource(
        id="ps1", person_id="p1", source_type=SourceType.TELEGRAM,
        source_identifier="@arestovich",
        last_collected_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    doc = RawDocument(
        id="tg:arestovich:1", person_id="p1", source_type=SourceType.TELEGRAM,
        url="https://t.me/arestovich/1",
        published_at=datetime(2024, 1, 5, tzinfo=UTC), raw_text="Post",
    )
    source_repo = FakeSourceRepo()
    await source_repo.save_person_source(person_source)
    prediction_repo = FakePredictionRepo()
    pred = Prediction(
        id="pred-1", document_id="tg:arestovich:1", person_id="p1",
        claim_text="claim", prediction_date=date(2024, 1, 1),
    )
    extractor = MagicMock()
    extractor.extract = AsyncMock(side_effect=[[pred]])
    factory, _ = _stub_session_factory()

    orchestrator = IngestionOrchestrator(
        session_factory=factory, source_repo=source_repo,
        prediction_repo=prediction_repo, extractor=extractor,
        embedder=None, sources={SourceType.TELEGRAM: MockSource([doc])},
    )

    await orchestrator.run_cycle()

    assert len(prediction_repo._predictions) == 1
    assert prediction_repo._predictions[0].embedding is None
