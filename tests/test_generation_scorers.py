from datetime import date

from eval_common.models import EvalCase, EvalRun
from generation.gen_models import ExpectedSource, GenerationInput, GenerationLabels
from generation.scorers import (
    CitationCoverageScorer,
    CitationPrecisionScorer,
    CompletenessScorer,
    FaithfulnessScorer,
    citation_coverage,
)
from prophet_checker.models.domain import (
    AnswerResult,
    CitationRef,
    Prediction,
    RetrievedPrediction,
)
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


# --- цитати (Task 11) ---


def _cit_run(answer: str, refs: list[CitationRef], expected_ids: tuple[str, ...] = ("p1",)):
    expected = []
    for pid in expected_ids:
        expected.append(ExpectedSource(prediction=_pred(pid)))
    labels = GenerationLabels(
        answerable=True, expected_sources=expected, category="single_source"
    )
    case = EvalCase(id="c1", input=GenerationInput(question="q"), labels=labels)
    result = AnswerResult(
        query="q",
        answer=answer,
        sources=[RetrievedPrediction(prediction=_pred("p1"), distance=0.1, rank=1)],
        refs=refs,
    )
    return EvalRun(case=case, result=result, latency_s=0.1)


def test_coverage_is_cited_over_expected():
    refs = [CitationRef(marker=1, prediction_id="p1", document_id="d", offset=0)]

    assert citation_coverage(refs, ["p1", "p2"]) == 0.5


def test_coverage_counts_distinct_predictions_only():
    refs = [
        CitationRef(marker=1, prediction_id="p1", document_id="d", offset=0),
        CitationRef(marker=1, prediction_id="p1", document_id="d", offset=9),
    ]

    assert citation_coverage(refs, ["p1", "p2"]) == 0.5


def test_coverage_none_without_expected_sources():
    assert citation_coverage([], []) is None


async def test_precision_all_supported():
    refs = [CitationRef(marker=1, prediction_id="p1", document_id="d", offset=8)]
    judge = _SeqJudge('{"supported": true, "reason": ""}')

    card = await CitationPrecisionScorer(judge).score(_cit_run("Речення [1].", refs))

    assert card.score == 1.0


async def test_precision_counts_unsupported():
    refs = [
        CitationRef(marker=1, prediction_id="p1", document_id="d", offset=8),
        CitationRef(marker=2, prediction_id="p1", document_id="d", offset=20),
    ]
    judge = _SeqJudge(
        '{"supported": true, "reason": ""}', '{"supported": false, "reason": "інша тема"}'
    )

    card = await CitationPrecisionScorer(judge).score(_cit_run("Речення [1]. Друге [2].", refs))

    assert card.score == 0.5


async def test_precision_na_without_refs():
    card = await CitationPrecisionScorer(_SeqJudge()).score(_cit_run("без маркерів", []))

    assert card.score is None


async def test_precision_na_on_sut_error():
    labels = GenerationLabels(answerable=True, expected_sources=[], category="single_source")
    case = EvalCase(id="c1", input=GenerationInput(question="q"), labels=labels)
    run = EvalRun(case=case, result=None, latency_s=0.1)

    card = await CitationPrecisionScorer(_SeqJudge()).score(run)

    assert card.score is None


async def test_coverage_scorer_reads_expected_from_labels():
    refs = [CitationRef(marker=1, prediction_id="p1", document_id="d", offset=0)]

    card = await CitationCoverageScorer().score(_cit_run("текст [1]", refs, ("p1", "p2")))

    assert card.score == 0.5
