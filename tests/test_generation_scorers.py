from datetime import date

from eval_common.models import EvalCase, EvalRun
from generation.gen_models import GenerationInput, GenerationLabels
from generation.scorers import CompletenessScorer, FaithfulnessScorer
from prophet_checker.models.domain import AnswerResult, Prediction, RetrievedPrediction
from prophet_checker.query.answer_orchestrator import REFUSAL_NO_DATA


class _SeqJudge:
    """Повертає задані відповіді по черзі (для різних вердиктів на послідовні виклики)."""

    id = "seq"

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self._i = 0

    async def assess(self, prompt: str, *, system: str) -> str:
        r = self._responses[self._i]
        self._i += 1
        return r


def _pred(pid: str) -> Prediction:
    return Prediction(
        id=pid,
        document_id="d",
        person_id="x",
        claim_text=f"claim {pid}",
        prediction_date=date(2024, 1, 1),
    )


def _run(answer, *, answerable, category, source_ids=("p1",)):
    labels = GenerationLabels(answerable=answerable, expected_sources=[], category=category)
    case = EvalCase(id="c1", input=GenerationInput(question="q"), labels=labels)
    result = None
    if answer is not None:
        result = AnswerResult(
            query="q",
            answer=answer,
            sources=[
                RetrievedPrediction(prediction=_pred(pid), distance=0.1, rank=i)
                for i, pid in enumerate(source_ids, 1)
            ],
        )
    return EvalRun(case=case, result=result, latency_s=0.1)


# --- faithfulness ---


async def test_faithfulness_na_on_sut_error():
    card = await FaithfulnessScorer(_SeqJudge()).score(
        _run(None, answerable=True, category="single_source")
    )
    assert card.score is None


async def test_faithfulness_na_on_offcorpus():
    judge = _SeqJudge('{"claims": [{"claim": "x", "supported": true}]}')
    card = await FaithfulnessScorer(judge).score(
        _run("щось", answerable=False, category="off_domain")
    )
    assert card.score is None


async def test_faithfulness_na_on_zero_claims():
    judge = _SeqJudge('{"claims": []}')
    card = await FaithfulnessScorer(judge).score(
        _run(REFUSAL_NO_DATA, answerable=True, category="single_source")
    )
    assert card.score is None


async def test_faithfulness_ratio():
    judge = _SeqJudge(
        '{"claims": [{"claim": "a", "supported": true}, {"claim": "b", "supported": false}]}'
    )
    card = await FaithfulnessScorer(judge).score(
        _run("відп", answerable=True, category="single_source")
    )
    assert card.score == 0.5
    assert len(card.detail.claims) == 2


# --- completeness ---


async def test_completeness_na_on_sut_error():
    card = await CompletenessScorer(_SeqJudge()).score(
        _run(None, answerable=True, category="single_source")
    )
    assert card.score is None


async def test_completeness_na_when_no_sources():
    # порожні sources (refusal / DB-miss) → N/A, а не recall=0
    run = EvalRun(
        case=EvalCase(
            id="c1",
            input=GenerationInput(question="q"),
            labels=GenerationLabels(answerable=True, expected_sources=[], category="single_source"),
        ),
        result=AnswerResult(query="q", answer=REFUSAL_NO_DATA, sources=[]),
        latency_s=0.1,
    )
    card = await CompletenessScorer(_SeqJudge()).score(run)
    assert card.score is None


async def test_completeness_recall_half():
    judge = _SeqJudge('{"covered": true}', '{"covered": false}')
    card = await CompletenessScorer(judge).score(
        _run("відп", answerable=True, category="synthesis", source_ids=("p1", "p2"))
    )
    assert card.score == 0.5
    assert [c.covered for c in card.detail.coverage] == [True, False]
    assert [c.prediction_id for c in card.detail.coverage] == ["p1", "p2"]
