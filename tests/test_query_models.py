from datetime import date

from prophet_checker.models.domain import (
    Prediction,
    QueryResult,
    RetrievedPrediction,
    VectorMatch,
)


def test_vector_match_fields():
    m = VectorMatch(prediction_id="p1", distance=0.12)
    assert m.prediction_id == "p1"
    assert m.distance == 0.12


def test_query_result_nests_retrieved_predictions():
    pred = Prediction(
        id="p1", document_id="d", person_id="x", claim_text="c", prediction_date=date(2024, 1, 1)
    )
    qr = QueryResult(
        query="q", results=[RetrievedPrediction(prediction=pred, distance=0.2, rank=1)]
    )
    assert qr.query == "q"
    assert qr.results[0].prediction.id == "p1"
    assert qr.results[0].rank == 1
    assert qr.results[0].distance == 0.2
