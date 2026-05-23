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
from prophet_checker.models.domain import Prediction, PredictionStatus

logger = logging.getLogger(__name__)


class PredictionExtractor:
    """Extracts verifiable predictions from raw text using an LLM."""

    def __init__(self, llm) -> None:
        self._llm = llm

    async def extract(
        self,
        text: str,
        person_id: str,
        document_id: str,
        person_name: str,
        published_date: str,
    ) -> list[Prediction]:
        try:
            prompt = build_extraction_prompt(
                text=text,
                person_name=person_name,
                published_date=published_date,
            )
            response = await self._llm.complete(prompt, system=get_extraction_system())
        except Exception:
            logger.exception("LLM call failed during extraction")
            return []

        raw_predictions = parse_extraction_response(response)
        if not raw_predictions:
            return []

        predictions: list[Prediction] = []
        for raw in raw_predictions:
            claim = raw.get("claim_text", "").strip()
            if not claim:
                continue

            situation = raw.get("situation")
            if not validate_situation(situation):
                logger.warning(
                    "Drop prediction — missing/empty situation: %r", claim[:60]
                )
                continue

            # Parse optional target_date
            target_date: date | None = None
            raw_target = raw.get("target_date")
            if raw_target:
                try:
                    target_date = date.fromisoformat(raw_target)
                except (ValueError, TypeError):
                    target_date = None

            # Parse prediction_date (fall back to published_date)
            raw_pred_date = raw.get("prediction_date") or published_date
            try:
                prediction_date = date.fromisoformat(raw_pred_date)
            except (ValueError, TypeError):
                prediction_date = date.fromisoformat(published_date)

            predictions.append(
                Prediction(
                    id=str(uuid4()),
                    person_id=person_id,
                    document_id=document_id,
                    claim_text=claim,
                    situation=situation,
                    prediction_date=prediction_date,
                    target_date=target_date,
                    topic=raw.get("topic", ""),
                    status=PredictionStatus.UNRESOLVED,
                    confidence=0.0,
                    evidence_url=None,
                    evidence_text=None,
                    embedding=None,
                )
            )

        return predictions
