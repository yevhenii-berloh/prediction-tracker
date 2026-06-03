from __future__ import annotations

from datetime import date, datetime

from prophet_checker.models.domain import (
    Prediction,
    PredictionStatus,
    PredictionStrength,
    PredictionValue,
)


def apply_verification_result(prediction: Prediction, result: dict, now: datetime) -> Prediction:
    status = PredictionStatus(result["status"])
    updates = {
        "status": status,
        "confidence": result["confidence"],
        "prediction_strength": PredictionStrength(result["prediction_strength"]),
        "prediction_value": PredictionValue(result["prediction_value"]),
        "evidence_text": result.get("evidence"),
        "verified_at": now,
        "verify_attempts": prediction.verify_attempts + 1,
        "last_verify_error": None,
        "last_verify_error_at": None,
        "next_check_at": None,
        "max_horizon": None,
    }
    if status == PredictionStatus.PREMATURE:
        retry_after = result.get("retry_after")
        if retry_after:
            updates["next_check_at"] = date.fromisoformat(retry_after)
        max_horizon = result.get("max_horizon")
        if max_horizon:
            updates["max_horizon"] = date.fromisoformat(max_horizon)
    return prediction.model_copy(update=updates)


def apply_verification_error(prediction: Prediction, exc: Exception, now: datetime) -> Prediction:
    return prediction.model_copy(update={
        "verify_attempts": prediction.verify_attempts + 1,
        "last_verify_error": f"{type(exc).__name__}: {exc}",
        "last_verify_error_at": now,
    })
