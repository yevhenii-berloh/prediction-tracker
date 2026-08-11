import json
from datetime import date

from eval_common.models import EvalCase
from prophet_checker.models.domain import Prediction

from extraction.eval_v2.eval_models import ExtractionResult, PostInput
from extraction.eval_v2.extraction_eval import build_report, judge_all_posts


class ScriptedJudge:
    id = "scripted"

    def __init__(self, responses):
        self._responses = list(responses)

    async def assess(self, prompt: str, *, system: str) -> str:
        return self._responses.pop(0) if self._responses else '{"missed": []}'


class BrokenJudge:
    id = "broken"

    async def assess(self, prompt: str, *, system: str) -> str:
        raise RuntimeError("судді погано")


_VALID = (
    '{"claim_grounded": true, "situation_grounded": true, '
    '"passes_rubric": true, "truncated": false}'
)


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


def _result(claim: str) -> ExtractionResult:
    return ExtractionResult(
        predictions=[
            Prediction(
                id=claim,
                document_id="p1",
                person_id="a",
                claim_text=claim,
                situation="s",
                prediction_date=date(2026, 6, 1),
            )
        ]
    )


async def test_end_to_end_on_fakes():
    cases = [_case("p1")]
    by_model = {
        "base": {"p1": _result("Ціни зростуть")},
        "cand": {"p1": _result("Ціни зростуть")},
    }
    judge = ScriptedJudge([_VALID, '{"missed": []}'])

    scores, judgements = await judge_all_posts(cases, by_model, judge, concurrency=1)
    report = build_report(
        cases, scores, judgements, ["base", "cand"], baseline_id="base", judge_id="scripted"
    )

    assert report.metrics.per_model["base"].coverage == 1.0
    assert report.metrics.per_model["cand"].coverage == 1.0
    assert report.metrics.winner in {"base", "cand"}
    assert report.metadata.n_cases == 1
    assert json.loads(report.model_dump_json())["metrics"]["baseline_model"] == "base"


async def test_every_card_carries_detail():
    """Урок citation-прогону: картка без detail робить результат недіагностовним."""
    cases = [_case("p1")]
    by_model = {"base": {"p1": _result("Ціни зростуть")}}
    judge = ScriptedJudge([_VALID, '{"missed": []}'])

    scores, judgements = await judge_all_posts(cases, by_model, judge, concurrency=1)
    report = build_report(cases, scores, judgements, ["base"], "base", "scripted")

    for card in report.runs[0].cards:
        assert card.detail is not None


async def test_a_post_the_judge_kills_does_not_kill_the_run():
    cases = [_case("p1"), _case("p2")]
    by_model = {"base": {"p1": _result("Ціни зростуть"), "p2": _result("Курс впаде")}}

    scores, judgements = await judge_all_posts(cases, by_model, BrokenJudge(), concurrency=1)

    assert len(scores) == 2
    assert len(judgements) == 2
    assert all(j.claims == [] or j.verdicts == [] for j in judgements)


async def test_model_that_extracted_nothing_still_appears_in_the_report():
    cases = [_case("p1")]
    by_model = {"base": {"p1": _result("Ціни зростуть")}, "silent": {}}
    judge = ScriptedJudge([_VALID, '{"missed": []}'])

    scores, judgements = await judge_all_posts(cases, by_model, judge, concurrency=1)
    report = build_report(cases, scores, judgements, ["base", "silent"], "base", "scripted")

    assert report.metrics.per_model["silent"].coverage == 0.0  # мовчання = провал покриття
    assert report.metrics.per_model["silent"].precision is None  # ділити нема на що


async def test_determinism_and_cost_land_on_the_metrics():
    cases = [_case("p1")]
    by_model = {"base": {"p1": _result("Ціни зростуть")}}
    judge = ScriptedJudge([_VALID, '{"missed": []}'])

    scores, judgements = await judge_all_posts(cases, by_model, judge, concurrency=1)
    report = build_report(
        cases,
        scores,
        judgements,
        ["base"],
        "base",
        "scripted",
        determinism={"base": 0.9},
        costs={"base": 0.0012},
    )

    assert report.metrics.per_model["base"].determinism == 0.9
    assert report.metrics.per_model["base"].cost_per_post == 0.0012
