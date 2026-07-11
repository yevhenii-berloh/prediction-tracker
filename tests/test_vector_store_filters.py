from datetime import date

from fakes import FakeVectorStore

from prophet_checker.models.domain import SearchFilters


async def _store_with_three() -> FakeVectorStore:
    store = FakeVectorStore()
    await store.store_embedding(
        "p1",
        [0.1],
        person_id="a1",
        prediction_date=date(2022, 3, 1),
        target_date=date(2023, 6, 1),
    )
    await store.store_embedding(
        "p2",
        [0.2],
        person_id="a2",
        prediction_date=date(2023, 5, 1),
        target_date=None,
    )
    await store.store_embedding(
        "p3",
        [0.3],
        person_id="a1",
        prediction_date=date(2024, 1, 1),
        target_date=date(2024, 2, 1),
    )
    return store


async def test_no_filters_returns_all():
    store = await _store_with_three()
    matches = await store.search_similar([0.0], limit=10)
    assert [m.prediction_id for m in matches] == ["p1", "p2", "p3"]


async def test_person_filter():
    store = await _store_with_three()
    matches = await store.search_similar([0.0], limit=10, filters=SearchFilters(person_id="a1"))
    assert [m.prediction_id for m in matches] == ["p1", "p3"]


async def test_prediction_date_range():
    store = await _store_with_three()
    filters = SearchFilters(
        prediction_date_from=date(2022, 1, 1), prediction_date_to=date(2022, 12, 31)
    )
    matches = await store.search_similar([0.0], limit=10, filters=filters)
    assert [m.prediction_id for m in matches] == ["p1"]


async def test_target_date_range_is_null_inclusive():
    # p2 має target_date=None → лишається (Р2); p3 поза діапазоном → відсікається
    store = await _store_with_three()
    filters = SearchFilters(target_date_from=date(2023, 1, 1), target_date_to=date(2023, 12, 31))
    matches = await store.search_similar([0.0], limit=10, filters=filters)
    assert [m.prediction_id for m in matches] == ["p1", "p2"]


async def test_filters_are_anded():
    store = await _store_with_three()
    filters = SearchFilters(person_id="a1", prediction_date_from=date(2024, 1, 1))
    matches = await store.search_similar([0.0], limit=10, filters=filters)
    assert [m.prediction_id for m in matches] == ["p3"]


async def test_last_filters_recorded():
    store = await _store_with_three()
    filters = SearchFilters(person_id="a1")
    await store.search_similar([0.0], limit=10, filters=filters)
    assert store.last_filters == filters
