from datetime import date

from eval_common.models import EvalCase, EvalRun
from generation.gen_models import ExpectedSource, GenerationInput, GenerationLabels
from prophet_checker.models.domain import Prediction, QueryResult, RetrievedPrediction
from rag.threshold_sweep import sweep_thresholds


def _pred(pid: str) -> Prediction:
    return Prediction(
        id=pid, document_id="d", person_id="x", claim_text=pid, prediction_date=date(2024, 1, 1)
    )


def _run(qid, answerable, category, expected_ids, matches):
    labels = GenerationLabels(
        answerable=answerable,
        expected_sources=[ExpectedSource(prediction=_pred(p)) for p in expected_ids],
        category=category,
    )
    case = EvalCase(id=qid, input=GenerationInput(question="q"), labels=labels)
    results = [
        RetrievedPrediction(prediction=_pred(mid), distance=md, rank=i)
        for i, (mid, md) in enumerate(matches, start=1)
    ]
    return EvalRun(case=case, result=QueryResult(query="q", results=results), latency_s=0.1)


def _runs():
    return [
        _run("a001", True, "single_source", ["p1"], [("p1", 0.20), ("p9", 0.55)]),
        _run("a002", True, "single_source", ["p2"], [("p7", 0.30), ("p2", 0.45), ("p5", 0.60)]),
        _run("s001", True, "synthesis", ["p4", "p6"], [("p4", 0.25), ("p6", 0.48), ("p8", 0.70)]),
        _run("o001", False, "off_domain", [], [("p3", 0.85)]),
        _run("n001", False, "near_domain", [], [("p5", 0.52)]),
    ]


def _pt(report, t):
    return next(p for p in report.curve if p.threshold == t)


def test_sweep_curve_and_choice():
    report = sweep_thresholds(_runs(), recall_target=0.9)

    # @0.30 (плато <0.45): усі answerable відповідають, але recall 0.5; обидва off — відмова
    p30 = _pt(report, 0.30)
    assert p30.answer_rate == 1.0 and p30.recall == 0.5 and p30.refusal_rate == 1.0
    # @0.48 — солодка точка: recall 1.0 і off-refusal 1.0
    p48 = _pt(report, 0.48)
    assert p48.answer_rate == 1.0 and p48.recall == 1.0 and p48.refusal_rate == 1.0
    # @0.52 — near_domain протік: recall 1.0, refusal 0.5
    p52 = _pt(report, 0.52)
    assert p52.recall == 1.0 and p52.refusal_rate == 0.5

    assert report.chosen_threshold == 0.48
    assert report.recall_target == 0.9


def test_sweep_no_threshold_meets_recall():
    # очікуване джерело надто далеко → recall <0.9 за будь-якого T → chosen None
    # (pX узагалі не знайдено серед matches)
    runs = [_run("a", True, "single_source", ["pX"], [("pY", 0.10)])]
    report = sweep_thresholds(runs, recall_target=0.9)
    assert report.chosen_threshold is None
