from __future__ import annotations

import logging
from datetime import date
from uuid import uuid4

from prophet_checker.llm.prompts import (
    build_extraction_prompt,
    get_extraction_system,
    parse_extraction_response,
    validate_situation,
)
from prophet_checker.models.domain import (
    ExtractionOutcome,
    Prediction,
    PredictionStatus,
)

logger = logging.getLogger(__name__)


class PredictionExtractor:
    """Extracts verifiable predictions from raw text using an LLM."""

    def __init__(self, llm, system_prompt: str | None = None) -> None:
        self._llm = llm
        self._system_prompt = system_prompt

    async def extract(
        self,
        text: str,
        person_id: str,
        document_id: str,
        person_name: str,
        published_date: str,
    ) -> ExtractionOutcome:
        try:
            prompt = build_extraction_prompt(
                text=text,
                person_name=person_name,
                published_date=published_date,
            )
            response = await self._llm.complete(
                prompt, system=self._system_prompt or get_extraction_system()
            )
        except Exception as exc:
            logger.exception("LLM call failed during extraction")
            return ExtractionOutcome(failed=True, error=type(exc).__name__)

        try:
            raw_predictions = parse_extraction_response(response)
        except ValueError:
            # extract() лишається тотальною: інжест ловить failed, а не виняток
            logger.exception("Unparsable extraction response for %s", document_id)
            return ExtractionOutcome(failed=True, error="UnparsableResponse")

        if not raw_predictions:
            return ExtractionOutcome()

        predictions: list[Prediction] = []
        for raw in raw_predictions:
            prediction = _build_prediction(raw, person_id, document_id, published_date)
            if prediction is not None:
                predictions.append(prediction)

        return ExtractionOutcome(predictions=predictions)


def _parse_date(value: str | None, fallback: str) -> date:
    try:
        return date.fromisoformat(value or fallback)
    except (ValueError, TypeError):
        return date.fromisoformat(fallback)


def _parse_optional_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _build_prediction(
    raw: dict, person_id: str, document_id: str, published_date: str
) -> Prediction | None:
    """Один сирий запис → Prediction. None, якщо запис не проходить контракт."""
    claim = raw.get("claim_text", "").strip()
    if not claim:
        return None

    situation = raw.get("situation")
    if not validate_situation(situation):
        logger.warning("Drop prediction — missing/empty situation: %r", claim[:60])
        return None

    return Prediction(
        id=str(uuid4()),
        person_id=person_id,
        document_id=document_id,
        claim_text=claim,
        situation=situation,
        prediction_date=_parse_date(raw.get("prediction_date"), published_date),
        target_date=_parse_optional_date(raw.get("target_date")),
        topic=raw.get("topic", ""),
        status=PredictionStatus.UNRESOLVED,
        confidence=0.0,
        evidence_url=None,
        evidence_text=None,
        embedding=None,
    )
