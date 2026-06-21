from datetime import date

from prophet_checker.analysis.embedding_text import embedding_text
from prophet_checker.models.domain import Prediction


def _pred(claim: str, situation):
    return Prediction(
        id="x",
        document_id="d",
        person_id="p",
        claim_text=claim,
        situation=situation,
        prediction_date=date(2024, 1, 1),
    )


def test_concat_when_situation_present():
    assert embedding_text(_pred("C", "S")) == "C\nS"


def test_fallback_to_claim_when_situation_none():
    assert embedding_text(_pred("C", None)) == "C"


def test_fallback_to_claim_when_situation_blank():
    assert embedding_text(_pred("C", "   ")) == "C"
