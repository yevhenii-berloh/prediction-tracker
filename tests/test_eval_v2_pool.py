from datetime import date

from eval_common.fakes import FakeJudge
from prophet_checker.models.domain import Prediction

from extraction.eval_v2.pool import normalize, pool_claims


class CountingJudge:
    """FakeJudge, який ще й рахує виклики — пре-прохід має уникати судді."""

    id = "counting"

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    async def assess(self, prompt: str, *, system: str) -> str:
        self.calls += 1
        return self._response


def _prediction(claim: str, situation: str = "ситуація") -> Prediction:
    return Prediction(
        id=claim,
        document_id="p1",
        person_id="Арестович",
        claim_text=claim,
        situation=situation,
        prediction_date=date(2026, 6, 1),
    )


def test_normalize_strips_case_space_punctuation():
    assert normalize("  Ціни  ЗРОСТУТЬ! ") == normalize("ціни зростуть")


def test_normalize_keeps_different_claims_apart():
    assert normalize("Ціни зростуть") != normalize("Ціни не зростуть")


async def test_exact_duplicates_merge_without_the_judge():
    judge = CountingJudge('{"clusters": [[0]]}')
    by_model = {"a": [_prediction("Ціни зростуть")], "b": [_prediction("ціни зростуть!")]}

    pooled = await pool_claims("p1", "текст", by_model, judge)

    claims = pooled.claims

    assert len(claims) == 1
    assert sorted(claims[0].models) == ["a", "b"]
    assert judge.calls == 0  # один claim після пре-проходу — кластеризувати нема чого


async def test_judge_clusters_non_identical_claims():
    judge = FakeJudge('{"clusters": [[0, 1]]}')
    by_model = {"a": [_prediction("Ціни зростуть")], "b": [_prediction("Буде зростання цін")]}

    pooled = await pool_claims("p1", "текст", by_model, judge)

    claims = pooled.claims

    assert len(claims) == 1
    assert sorted(claims[0].models) == ["a", "b"]


async def test_judge_keeps_negation_apart():
    """Пара, на якій програє будь-який поріг схожості."""
    judge = FakeJudge('{"clusters": [[0], [1]]}')
    by_model = {"a": [_prediction("Ціни зростуть")], "b": [_prediction("Ціни не зростуть")]}

    pooled = await pool_claims("p1", "текст", by_model, judge)

    claims = pooled.claims

    assert len(claims) == 2


async def test_no_claims_skips_the_judge_entirely():
    judge = CountingJudge("не мало викликатись")

    pooled = await pool_claims("p1", "текст", {"a": [], "b": []}, judge)

    claims = pooled.claims

    assert claims == []
    assert judge.calls == 0


async def test_claim_missing_from_clusters_survives_as_singleton():
    judge = FakeJudge('{"clusters": [[0]]}')  # суддя загубив індекс 1
    by_model = {"a": [_prediction("Перше")], "b": [_prediction("Друге")]}

    pooled = await pool_claims("p1", "текст", by_model, judge)

    claims = pooled.claims

    assert len(claims) == 2


async def test_unparsable_clustering_leaves_claims_separate():
    judge = FakeJudge("суддя щось намолов")
    by_model = {"a": [_prediction("Перше")], "b": [_prediction("Друге")]}

    pooled = await pool_claims("p1", "текст", by_model, judge)

    claims = pooled.claims

    assert len(claims) == 2


async def test_transport_failure_in_clustering_leaves_claims_separate():
    """Кластеризація — перший виклик судді на пості; її збій не має вбивати пост."""

    class DeadTransportJudge:
        id = "dead"

        async def assess(self, prompt: str, *, system: str) -> str:
            raise RuntimeError("rate limit exceeded")

    by_model = {"a": [_prediction("Перше")], "b": [_prediction("Друге")]}

    pooled = await pool_claims("p1", "текст", by_model, DeadTransportJudge())

    assert len(pooled.claims) == 2
    assert pooled.clustering_failed is True  # reference set після фолбеку завищений


async def test_claim_ids_are_unique_within_a_post():
    judge = FakeJudge('{"clusters": [[0], [1], [2]]}')
    by_model = {"a": [_prediction("Перше"), _prediction("Друге"), _prediction("Третє")]}

    pooled = await pool_claims("p1", "текст", by_model, judge)

    claims = pooled.claims

    assert len({c.claim_id for c in claims}) == 3
    assert all(c.claim_id.startswith("p1:") for c in claims)


async def test_empty_claim_text_is_dropped():
    judge = FakeJudge('{"clusters": [[0]]}')
    by_model = {"a": [_prediction("   "), _prediction("Ціни зростуть")]}

    pooled = await pool_claims("p1", "текст", by_model, judge)

    claims = pooled.claims

    assert [c.claim_text for c in claims] == ["Ціни зростуть"]
