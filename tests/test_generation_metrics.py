from datetime import date

from eval_common.models import EvalCase, EvalRun, ScoreCard, ScoredRun
from generation.gen_models import GenerationInput, GenerationLabels, RefusalDetail
from generation.metrics import aggregate
from prophet_checker.models.domain import AnswerResult, Prediction, RetrievedPrediction


def _pred():
    return Prediction(
        id="p", document_id="d", person_id="x", claim_text="c", prediction_date=date(2024, 1, 1)
    )


def _scored(category, answerable, *, faith=None, recall=None, refused=False, error=False):
    labels = GenerationLabels(answerable=answerable, category=category)
    case = EvalCase(id="c", input=GenerationInput(question="q"), labels=labels)

    if error:
        run = EvalRun(case=case, result=None, latency_s=0.1, error="RuntimeError")
        cards = [
            ScoreCard(scorer=name, score=None)
            for name in ("faithfulness", "refusal", "completeness")
        ]
        return ScoredRun(run=run, cards=cards)

    result = AnswerResult(
        query="q",
        answer="a",
        sources=[RetrievedPrediction(prediction=_pred(), distance=0.1, rank=1)],
    )
    run = EvalRun(case=case, result=result, latency_s=0.1)
    correct = (answerable and not refused) or (not answerable and refused)
    cards = [
        ScoreCard(scorer="faithfulness", score=faith),
        ScoreCard(
            scorer="refusal",
            score=1.0 if correct else 0.0,
            detail=RefusalDetail(refused=refused, answerable=answerable, category=category),
        ),
        ScoreCard(scorer="completeness", score=recall),
    ]
    return ScoredRun(run=run, cards=cards)


def test_aggregate_means_and_refusal_rates():
    scored = [
        _scored("single_source", True, faith=1.0, recall=1.0, refused=False),
        _scored("single_source", True, faith=0.5, recall=0.0, refused=False),
        _scored("off_domain", False, refused=True),  # correct refusal
        _scored("near_domain", False, refused=False),  # false answer
        _scored("single_source", True, error=True),  # SUT error
    ]
    m = aggregate(scored)
    assert m.n_total == 5
    assert m.n_errors == 1
    assert m.n_answered == 3  # 2 answerable answered + 1 off-corpus answered
    assert m.n_refused == 1
    assert m.faithfulness_mean == 0.75
    assert m.hallucination_rate == 0.25
    assert m.recall_mean == 0.5
    # refusal: 2 answerable answered (correct), 1 off refused (correct), 1 off answered (wrong) = 3/4
    assert m.refusal_accuracy == 0.75
    assert m.over_refusal_rate == 0.0  # no answerable refused
    assert m.false_answer_rate == 0.5  # 1 of 2 off-corpus answered
    assert m.by_category["single_source"].faithfulness_mean == 0.75


def test_aggregate_empty():
    m = aggregate([])
    assert m.n_total == 0
    assert m.faithfulness_mean is None
    assert m.refusal_accuracy == 0.0
