from datetime import date, datetime

from prophet_checker.models.domain import (
    Person,
    PersonSource,
    Prediction,
    PredictionStatus,
    RawDocument,
    SourceType,
)
from fakes import (
    FakePersonRepo,
    FakePredictionRepo,
    FakeSourceRepo,
    FakeVectorStore,
)


async def test_person_repo_round_trip():
    repo = FakePersonRepo()
    person = Person(id="1", name="Арестович", description="Оглядач")
    await repo.save(person)
    result = await repo.get_by_id("1")
    assert result is not None
    assert result.name == "Арестович"


async def test_source_repo_save_and_query():
    repo = FakeSourceRepo()
    ps = PersonSource(
        id="1", person_id="1", source_type=SourceType.TELEGRAM, source_identifier="@arest"
    )
    await repo.save_person_source(ps)
    sources = await repo.get_person_sources("1", SourceType.TELEGRAM)
    assert len(sources) == 1
    assert sources[0].source_identifier == "@arest"


async def test_source_repo_last_collected_at():
    repo = FakeSourceRepo()
    doc1 = RawDocument(
        id="1",
        person_id="1",
        source_type=SourceType.TELEGRAM,
        url="u1",
        published_at=datetime(2023, 1, 1),
        raw_text="text",
        collected_at=datetime(2024, 1, 1),
    )
    doc2 = RawDocument(
        id="2",
        person_id="1",
        source_type=SourceType.TELEGRAM,
        url="u2",
        published_at=datetime(2023, 2, 1),
        raw_text="text",
        collected_at=datetime(2024, 2, 1),
    )
    await repo.save_document(doc1)
    await repo.save_document(doc2)
    last = await repo.get_last_collected_at("1", SourceType.TELEGRAM)
    assert last == datetime(2024, 2, 1)


async def test_source_repo_last_collected_at_empty():
    repo = FakeSourceRepo()
    last = await repo.get_last_collected_at("1", SourceType.TELEGRAM)
    assert last is None


async def test_prediction_repo_save_and_query():
    repo = FakePredictionRepo()
    pred = Prediction(
        id="1",
        document_id="d1",
        person_id="1",
        claim_text="Test prediction",
        prediction_date=date(2023, 1, 1),
    )
    await repo.save(pred)
    results = await repo.get_by_person("1")
    assert len(results) == 1
    assert results[0].claim_text == "Test prediction"


async def test_prediction_repo_filter_by_status():
    repo = FakePredictionRepo()
    p1 = Prediction(
        id="1",
        document_id="d1",
        person_id="1",
        claim_text="Pred 1",
        prediction_date=date(2023, 1, 1),
        status=PredictionStatus.CONFIRMED,
    )
    p2 = Prediction(
        id="2",
        document_id="d2",
        person_id="1",
        claim_text="Pred 2",
        prediction_date=date(2023, 2, 1),
        status=PredictionStatus.REFUTED,
    )
    await repo.save(p1)
    await repo.save(p2)
    confirmed = await repo.get_by_person("1", status=PredictionStatus.CONFIRMED)
    assert len(confirmed) == 1
    assert confirmed[0].id == "1"


async def test_vector_store_search():
    store = FakeVectorStore()
    await store.store_embedding("p1", [0.1, 0.2, 0.3])
    await store.store_embedding("p2", [0.4, 0.5, 0.6])
    results = await store.search_similar([0.1, 0.2, 0.3], limit=1)
    assert len(results) == 1
    assert results[0].prediction_id == "p1"
    assert isinstance(results[0].distance, float)


async def test_get_by_ids_preserves_order_and_skips_missing():
    repo = FakePredictionRepo()
    await repo.save(
        Prediction(
            id="a", document_id="d", person_id="1", claim_text="A", prediction_date=date(2023, 1, 1)
        )
    )
    await repo.save(
        Prediction(
            id="b", document_id="d", person_id="1", claim_text="B", prediction_date=date(2023, 1, 1)
        )
    )
    got = await repo.get_by_ids(["b", "missing", "a"])
    assert [p.id for p in got] == ["b", "a"]
