"""Tests for scripts/evaluate_detection.py — Task 13 Detection Evaluation.

Target module does not exist yet (TDD Step 1). All tests are expected to FAIL
on ImportError until scripts/evaluate_detection.py is implemented in Step 2.

Test groups:
    A — compute_metrics(): pure function, no I/O (6 tests)
    B — classify_post():   extractor call bridge (4 tests)
    D — run_evaluation_for_model(): orchestration (4 tests)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from datetime import date

from prophet_checker.models.domain import ExtractionOutcome, Prediction

from extraction.detection_eval import (
    classify_post,
    compute_metrics,
    run_evaluation_for_model,
)


# =============================================================================
# Group A — compute_metrics(gold: list[bool], preds: list[bool]) -> dict
# =============================================================================
# Contract: pure function. Returns dict with keys:
#   precision, recall, f1, confusion={"TP","FP","FN","TN"}, total.
# Guards:
#   precision = TP/(TP+FP), 0.0 if denominator=0
#   recall    = TP/(TP+FN), None if denominator=0 (undefined: no positives in gold)
#   f1        = 2PR/(P+R) if both >0 and recall is not None, else 0.0


def test_compute_metrics_perfect_classifier():
    """Baseline: every prediction matches gold → P=R=F1=1.0."""
    metrics = compute_metrics(
        gold=[True, False, True, False],
        preds=[True, False, True, False],
    )
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["confusion"] == {"TP": 2, "FP": 0, "FN": 0, "TN": 2}
    assert metrics["total"] == 4


def test_compute_metrics_all_wrong():
    """Worst case: every prediction inverted → P=R=F1=0.0 without division error."""
    metrics = compute_metrics(
        gold=[True, False, True, False],
        preds=[False, True, False, True],
    )
    assert metrics["precision"] == 0.0
    assert metrics["recall"] == 0.0
    assert metrics["f1"] == 0.0
    assert metrics["confusion"] == {"TP": 0, "FP": 2, "FN": 2, "TN": 0}


def test_compute_metrics_balanced_confusion():
    """Realistic mixed case: TP=2, FP=1, FN=1, TN=1 → P=R=F1≈0.667."""
    metrics = compute_metrics(
        gold=[True, True, False, False, True],
        preds=[True, False, True, False, True],
    )
    assert metrics["confusion"] == {"TP": 2, "FP": 1, "FN": 1, "TN": 1}
    assert metrics["precision"] == pytest.approx(2 / 3, abs=1e-3)
    assert metrics["recall"] == pytest.approx(2 / 3, abs=1e-3)
    assert metrics["f1"] == pytest.approx(2 / 3, abs=1e-3)


def test_compute_metrics_no_positives_in_gold():
    """Edge: gold has 0 positives → recall undefined (None), precision=0 if any FP."""
    metrics = compute_metrics(
        gold=[False, False, False],
        preds=[False, False, True],
    )
    assert metrics["recall"] is None  # undefined: 0 / (0+0) cannot be computed
    assert metrics["precision"] == 0.0  # 0 TP / (0 TP + 1 FP)
    assert metrics["f1"] == 0.0  # convention: 0 when recall undefined
    assert metrics["confusion"] == {"TP": 0, "FP": 1, "FN": 0, "TN": 2}


def test_compute_metrics_no_predictions_made():
    """Edge: extractor predicts nothing for all posts → precision guarded to 0.0."""
    metrics = compute_metrics(
        gold=[True, False],
        preds=[False, False],
    )
    assert metrics["precision"] == 0.0  # 0 TP / (0 TP + 0 FP) → guarded
    assert metrics["recall"] == 0.0  # 0 TP / (0 TP + 1 FN)
    assert metrics["f1"] == 0.0
    assert metrics["confusion"] == {"TP": 0, "FP": 0, "FN": 1, "TN": 1}


def test_compute_metrics_raises_on_mismatched_lengths():
    """Defensive: mismatched input lengths signal caller bug → fail loud with ValueError."""
    with pytest.raises(ValueError):
        compute_metrics(gold=[True, False], preds=[True])


# =============================================================================
# Group B — classify_post(extractor, post: dict) -> bool | None
# =============================================================================
# Contract: returns True if extractor finds ≥1 prediction, False if 0, None on error.
# `None` is critical — it separates API failures from legitimate "no predictions"
# so compute_metrics() never sees error rows.



def _fake_prediction() -> Prediction:
    """Мінімальний валідний Prediction — ExtractionOutcome типізований, object() не пройде."""
    return Prediction(
        id="x",
        document_id="d",
        person_id="p",
        claim_text="щось станеться",
        situation="s",
        prediction_date=date(2026, 1, 1),
    )

async def test_classify_post_yes_when_extractor_returns_predictions():
    """Extractor returns ≥1 prediction → classify_post returns True."""
    mock_extractor = MagicMock()
    mock_extractor.extract = AsyncMock(return_value=ExtractionOutcome(predictions=[_fake_prediction()]))
    post = {
        "id": "p1",
        "person_name": "Арестович",
        "published_at": "2024-10-08",
        "text": "some text with a prediction",
    }
    result = await classify_post(mock_extractor, post)
    assert result is True


async def test_classify_post_no_when_extractor_returns_empty():
    """Extractor returns empty list → classify_post returns False (NOT None)."""
    mock_extractor = MagicMock()
    mock_extractor.extract = AsyncMock(return_value=ExtractionOutcome())
    post = {
        "id": "p1",
        "person_name": "Арестович",
        "published_at": "2024-10-08",
        "text": "pure news report",
    }
    result = await classify_post(mock_extractor, post)
    assert result is False


async def test_classify_post_none_on_extractor_error():
    """Extractor raises → classify_post returns None (so row excluded from metrics)."""
    mock_extractor = MagicMock()
    mock_extractor.extract = AsyncMock(side_effect=RuntimeError("API unavailable"))
    post = {
        "id": "p1",
        "person_name": "Арестович",
        "published_at": "2024-10-08",
        "text": "any text",
    }
    result = await classify_post(mock_extractor, post)
    assert result is None  # NOT False — must be distinguishable for error tracking


async def test_classify_post_forwards_all_required_args_to_extractor():
    """classify_post must pass all 5 args to extractor.extract() — silent bridge bugs."""
    mock_extractor = MagicMock()
    mock_extractor.extract = AsyncMock(return_value=ExtractionOutcome())
    post = {
        "id": "O_Arestovich_official_6808",
        "person_name": "Арестович",
        "published_at": "2025-01-23",
        "text": "Sample Ukrainian text with Cyrillic",
    }
    await classify_post(mock_extractor, post)
    mock_extractor.extract.assert_called_once_with(
        text="Sample Ukrainian text with Cyrillic",
        person_id="Арестович",
        document_id="O_Arestovich_official_6808",
        person_name="Арестович",
        published_date="2025-01-23",
    )


# =============================================================================
# Group D — run_evaluation_for_model(model_id, gold_labels, posts, ...) -> dict
# =============================================================================
# Contract: orchestrates classify_post() over filtered (author, gold-labeled) posts,
# computes metrics, builds report with FP/FN examples for error analysis.
# Report keys: model, author_filter, n_evaluated, n_errors, precision, recall, f1,
#              confusion, false_positives[], false_negatives[], errors[], total.
#
# Dependency injection via `extractor_factory: Callable[[str], extractor]` — so
# tests don't need API keys or real LLMClient instances.


async def test_run_evaluation_computes_metrics_from_mixed_results():
    """End-to-end happy path: orchestration → metrics correctly populated."""
    posts = [
        {"id": "p1", "person_name": "Арестович", "published_at": "2024-01-01",
         "text": "text 1 contains prediction about future"},
        {"id": "p2", "person_name": "Арестович", "published_at": "2024-01-02",
         "text": "text 2 is plain news"},
        {"id": "p3", "person_name": "Арестович", "published_at": "2024-01-03",
         "text": "text 3 contains prediction about economy"},
    ]
    gold = [
        {"id": "p1", "has_prediction": True},
        {"id": "p2", "has_prediction": False},
        {"id": "p3", "has_prediction": True},
    ]

    def fake_extract(*, text, **kw):
        preds = [_fake_prediction()] if "prediction" in text else []
        return ExtractionOutcome(predictions=preds)

    def make_extractor(model_id):
        m = MagicMock()
        m.extract = AsyncMock(side_effect=fake_extract)
        return m

    report = await run_evaluation_for_model(
        model_id="test-model",
        gold_labels=gold,
        posts=posts,
        author_filter="Арестович",
        extractor_factory=make_extractor,
    )

    assert report["model"] == "test-model"
    assert report["n_evaluated"] == 3
    assert report["n_errors"] == 0
    assert report["confusion"] == {"TP": 2, "FP": 0, "FN": 0, "TN": 1}
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["f1"] == 1.0


async def test_run_evaluation_filters_by_author():
    """Posts not matching author_filter must be excluded from evaluation."""
    posts = [
        {"id": "p1", "person_name": "Арестович", "published_at": "2024-01-01", "text": "a"},
        {"id": "p2", "person_name": "Арестович", "published_at": "2024-01-02", "text": "b"},
        {"id": "p3", "person_name": "Арестович", "published_at": "2024-01-03", "text": "c"},
        {"id": "p4", "person_name": "Гордон",    "published_at": "2024-01-04", "text": "d"},
        {"id": "p5", "person_name": "Подоляк",   "published_at": "2024-01-05", "text": "e"},
    ]
    gold = [{"id": f"p{i}", "has_prediction": False} for i in range(1, 6)]

    def make_extractor(model_id):
        m = MagicMock()
        m.extract = AsyncMock(return_value=ExtractionOutcome())  # always NO
        return m

    report = await run_evaluation_for_model(
        model_id="test-model",
        gold_labels=gold,
        posts=posts,
        author_filter="Арестович",
        extractor_factory=make_extractor,
    )

    assert report["n_evaluated"] == 3  # only 3 Arestovich posts counted
    assert report["confusion"]["TN"] == 3
    assert report["author_filter"] == "Арестович"


async def test_run_evaluation_handles_error_rows_separately():
    """Extractor exceptions must go to report.errors[] and NOT pollute metrics."""
    posts = [
        {"id": f"p{i}", "person_name": "Арестович", "published_at": "2024-01-01",
         "text": f"text {i}"}
        for i in range(1, 5)
    ]
    gold = [{"id": f"p{i}", "has_prediction": False} for i in range(1, 5)]

    def fake_extract(*, document_id, **kw):
        if document_id in ("p3", "p4"):
            raise RuntimeError(f"API down for {document_id}")
        return ExtractionOutcome()

    def make_extractor(model_id):
        m = MagicMock()
        m.extract = AsyncMock(side_effect=fake_extract)
        return m

    report = await run_evaluation_for_model(
        model_id="test-model",
        gold_labels=gold,
        posts=posts,
        author_filter="Арестович",
        extractor_factory=make_extractor,
    )

    assert report["n_evaluated"] == 2  # only p1, p2 contributed to metrics
    assert report["n_errors"] == 2
    error_ids = {e["id"] for e in report["errors"]}
    assert error_ids == {"p3", "p4"}
    # Confusion matrix built only from successful classifications
    assert report["confusion"]["TP"] + report["confusion"]["FP"] \
        + report["confusion"]["FN"] + report["confusion"]["TN"] == 2


async def test_run_evaluation_report_contains_fp_fn_lists_with_text_previews():
    """FP/FN lists must include id + text_preview (first 200 chars) for Step 6 analysis."""
    long_text_fp = "X" * 300 + " — this is a false positive text"
    long_text_fn = "Y" * 300 + " — this is a false negative text"
    posts = [
        {"id": "fp1", "person_name": "Арестович", "published_at": "2024-01-01",
         "text": long_text_fp},
        {"id": "fn1", "person_name": "Арестович", "published_at": "2024-01-02",
         "text": long_text_fn},
    ]
    gold = [
        {"id": "fp1", "has_prediction": False},  # gold: NO
        {"id": "fn1", "has_prediction": True},   # gold: YES
    ]

    def fake_extract(*, document_id, **kw):
        # fp1: predict YES (→ false positive)
        # fn1: predict NO (→ false negative)
        preds = [_fake_prediction()] if document_id == "fp1" else []
        return ExtractionOutcome(predictions=preds)

    def make_extractor(model_id):
        m = MagicMock()
        m.extract = AsyncMock(side_effect=fake_extract)
        return m

    report = await run_evaluation_for_model(
        model_id="test-model",
        gold_labels=gold,
        posts=posts,
        author_filter="Арестович",
        extractor_factory=make_extractor,
    )

    assert len(report["false_positives"]) == 1
    assert report["false_positives"][0]["id"] == "fp1"
    assert len(report["false_positives"][0]["text_preview"]) == 200  # trimmed

    assert len(report["false_negatives"]) == 1
    assert report["false_negatives"][0]["id"] == "fn1"
    assert len(report["false_negatives"][0]["text_preview"]) == 200
