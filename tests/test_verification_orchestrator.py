from __future__ import annotations

from datetime import UTC, date, datetime

from prophet_checker.models.domain import (
    Prediction,
    PredictionStatus,
    PredictionStrength,
    PredictionValue,
)
from prophet_checker.verification.orchestrator import (
    apply_verification_error,
    apply_verification_result,
)

NOW = datetime(2026, 5, 31, tzinfo=UTC)


def _make_prediction(pid="p1", attempts=0):
    return Prediction(
        id=pid,
        document_id="d1",
        person_id="arestovich",
        claim_text="Контрнаступ почнеться влітку",
        situation="Обговорення літньої кампанії",
        prediction_date=date(2022, 1, 15),
        verify_attempts=attempts,
    )


def test_apply_result_confirmed():
    result = {
        "status": "confirmed", "confidence": 0.9, "prediction_strength": "low",
        "prediction_value": "high", "evidence": "Сталось у червні.",
        "retry_after": None, "max_horizon": None,
    }
    out = apply_verification_result(_make_prediction(), result, NOW)
    assert out.status == PredictionStatus.CONFIRMED
    assert out.confidence == 0.9
    assert out.prediction_strength == PredictionStrength.LOW
    assert out.prediction_value == PredictionValue.HIGH
    assert out.evidence_text == "Сталось у червні."
    assert out.verified_at == NOW
    assert out.verify_attempts == 1
    assert out.next_check_at is None
    assert out.max_horizon is None
    assert out.last_verify_error is None
    assert out.last_verify_error_at is None


def test_apply_result_premature_sets_next_check():
    result = {
        "status": "premature", "confidence": 0.6, "prediction_strength": "medium",
        "prediction_value": "high", "evidence": None,
        "retry_after": "2026-09-01", "max_horizon": "2027-01-01",
    }
    out = apply_verification_result(_make_prediction(), result, NOW)
    assert out.status == PredictionStatus.PREMATURE
    assert out.next_check_at == date(2026, 9, 1)
    assert out.max_horizon == date(2027, 1, 1)
    assert out.verified_at == NOW
    assert out.verify_attempts == 1


def test_apply_error_keeps_unverified():
    out = apply_verification_error(_make_prediction(attempts=1), ValueError("bad json"), NOW)
    assert out.verify_attempts == 2
    assert out.last_verify_error == "ValueError: bad json"
    assert out.last_verify_error_at == NOW
    assert out.verified_at is None
