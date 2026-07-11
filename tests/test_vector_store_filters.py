from datetime import date

from fakes import FakeVectorStore

from prophet_checker.models.domain import SearchFilters
from prophet_checker.storage.postgres import _filter_predicates


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


async def test_range_bounds_are_inclusive():
    # Дата рівно на межі from == to має включатись (семантика >=/<=)
    store = await _store_with_three()
    filters = SearchFilters(
        prediction_date_from=date(2022, 3, 1), prediction_date_to=date(2022, 3, 1)
    )
    matches = await store.search_similar([0.0], limit=10, filters=filters)
    assert [m.prediction_id for m in matches] == ["p1"]


async def test_limit_applies_after_filtering():
    # limit=1 при двох відфільтрованих збігах → рівно перший, не порожньо і не обидва
    store = await _store_with_three()
    filters = SearchFilters(person_id="a1")
    matches = await store.search_similar([0.0], limit=1, filters=filters)
    assert [m.prediction_id for m in matches] == ["p1"]

    # Перший запис (p1) не проходить фільтр → limit рахує збіги, а не проскановані рядки
    matches = await store.search_similar([0.0], limit=1, filters=SearchFilters(person_id="a2"))
    assert [m.prediction_id for m in matches] == ["p2"]


async def test_null_prediction_date_is_excluded_by_range():
    # prediction_date=None валить предикат як у SQL (null_inclusive=False)
    store = FakeVectorStore()
    await store.store_embedding(
        "p_null",
        [0.1],
        person_id="a1",
        prediction_date=None,
        target_date=None,
    )
    filters = SearchFilters(prediction_date_from=date(2022, 1, 1))
    matches = await store.search_similar([0.0], limit=10, filters=filters)
    assert matches == []


async def test_one_sided_range_only_from():
    # Тільки from без to: відсікає все раніше from, решта проходить
    store = await _store_with_three()
    filters = SearchFilters(prediction_date_from=date(2023, 1, 1))
    matches = await store.search_similar([0.0], limit=10, filters=filters)
    assert [m.prediction_id for m in matches] == ["p2", "p3"]


def _sql(filters: SearchFilters) -> str:
    return " ; ".join(str(p) for p in _filter_predicates(filters))


def test_predicates_empty_filters():
    assert _filter_predicates(SearchFilters()) == []


def test_predicates_person():
    sql = _sql(SearchFilters(person_id="a1"))
    assert "predictions.person_id =" in sql


def test_predicates_prediction_date_bounds():
    sql = _sql(
        SearchFilters(prediction_date_from=date(2022, 1, 1), prediction_date_to=date(2022, 12, 31))
    )
    assert "predictions.prediction_date >=" in sql
    assert "predictions.prediction_date <=" in sql


def test_predicates_target_date_null_inclusive():
    sql = _sql(SearchFilters(target_date_from=date(2023, 1, 1)))
    assert "predictions.target_date >=" in sql
    assert "predictions.target_date IS NULL" in sql
    assert " OR " in sql
