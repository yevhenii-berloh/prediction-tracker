from datetime import date

from eval_common.models import EvalCase
from prophet_checker.models.domain import Prediction

from extraction.eval_v2.determinism import (
    determinism_score,
    determinism_subsample,
    structural_signature,
)
from extraction.eval_v2.eval_models import ExtractionResult, PostInput


def _result(*claims: str, situation: str = "s", topic: str = "t") -> ExtractionResult:
    predictions = []
    for claim in claims:
        predictions.append(
            Prediction(
                id=claim,
                document_id="p1",
                person_id="a",
                claim_text=claim,
                situation=situation,
                topic=topic,
                prediction_date=date(2026, 6, 1),
            )
        )
    return ExtractionResult(predictions=predictions)


def _case(post_id: str) -> EvalCase:
    return EvalCase(
        id=post_id,
        input=PostInput(
            post_id=post_id,
            author="Арестович",
            channel="@c",
            text="текст",
            published_date="2026-06-01",
            stratum="prefilter",
        ),
    )


def test_whitespace_only_difference_is_still_deterministic():
    """Різниця у форматуванні — не недетермінізм моделі."""
    assert structural_signature(_result(" Ціни зростуть ")) == structural_signature(
        _result("Ціни зростуть")
    )


def test_order_matters():
    assert structural_signature(_result("a", "b")) != structural_signature(_result("b", "a"))


def test_situation_change_is_a_difference():
    assert structural_signature(_result("a", situation="x")) != structural_signature(
        _result("a", situation="y")
    )


def test_empty_output_twice_counts_as_deterministic():
    assert structural_signature(_result()) == structural_signature(_result())


def test_score_is_the_share_of_identical_posts():
    first = {"p1": _result("a"), "p2": _result("a"), "p3": _result("a")}
    second = {"p1": _result("a"), "p2": _result("b"), "p3": _result("a")}

    assert determinism_score(first, second) == 2 / 3


def test_score_ignores_posts_missing_from_one_run():
    first = {"p1": _result("a"), "p2": _result("a")}
    second = {"p1": _result("a")}  # p2 впав на другому прогоні

    assert determinism_score(first, second) == 1.0


def test_score_is_none_when_there_is_nothing_to_compare():
    assert determinism_score({}, {}) is None
    assert determinism_score({"p1": _result("a")}, {}) is None


def test_subsample_is_stable_across_calls():
    cases = [_case(f"p{i:03d}") for i in range(100)]

    first = [c.id for c in determinism_subsample(cases, 20)]
    second = [c.id for c in determinism_subsample(list(reversed(cases)), 20)]

    assert first == second  # спільна для всіх моделей і незмінна між прогонами
    assert len(first) == 20


def test_subsample_returns_everything_when_the_dataset_is_smaller():
    cases = [_case(f"p{i}") for i in range(5)]

    assert len(determinism_subsample(cases, 20)) == 5
