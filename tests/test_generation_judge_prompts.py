from datetime import date

from generation.judge_prompts import (
    build_citation_prompt,
    build_faithfulness_prompt,
    parse_citation_response,
    parse_completeness_response,
    parse_faithfulness_response,
)
from prophet_checker.models.domain import (
    Prediction,
    PredictionStatus,
    RetrievedPrediction,
)


def test_parse_faithfulness_response_plain_and_fenced():
    raw = '{"claims": [{"claim": "a", "supported": true, "reason": "r"}, {"claim": "b", "supported": false}]}'
    claims = parse_faithfulness_response(raw)
    assert len(claims) == 2
    assert claims[0].claim == "a" and claims[0].supported is True
    assert claims[1].supported is False
    fenced = '```json\n{"claims": []}\n```'
    assert parse_faithfulness_response(fenced) == []


def test_parse_completeness_response():
    covered, reason = parse_completeness_response('{"covered": true, "reason": "так"}')
    assert covered is True and reason == "так"
    covered, _ = parse_completeness_response('{"covered": false}')
    assert covered is False


def test_faithfulness_prompt_shows_judge_same_source_as_generator():
    # суддя faithfulness має бачити ТЕ САМЕ джерело, що й генератор (build_rag_prompt):
    # id/claim/status + date/target/confidence — інакше чесні echo дати/впевненості штучно карають
    pred = Prediction(
        id="p1",
        document_id="d",
        person_id="x",
        claim_text="контрнаступ не дійде до моря",
        situation="південь",
        prediction_date=date(2023, 6, 1),
        target_date=date(2023, 12, 31),
        status=PredictionStatus.REFUTED,
        confidence=0.7,
    )
    prompt = build_faithfulness_prompt(
        "якась відповідь", [RetrievedPrediction(prediction=pred, distance=0.2, rank=1)]
    )
    assert "p1" in prompt
    assert "контрнаступ не дійде до моря" in prompt
    assert "refuted" in prompt
    assert "2023-06-01" in prompt  # date — раніше суддя цього НЕ бачив
    assert "2023-12-31" in prompt  # target — раніше суддя цього НЕ бачив
    assert "0.7" in prompt  # confidence — раніше суддя цього НЕ бачив


def test_faithfulness_prompt_treats_status_as_verdict_authority():
    # вердикт ("прогноз справдився/ні") ґрунтується на полі status — суддя має приймати його
    # як авторитет, інакше штрафує чесний переказ вердикту як «непідкріплений»
    pred = Prediction(
        id="p1",
        document_id="d",
        person_id="x",
        claim_text="контрнаступ не дійде до моря",
        prediction_date=date(2023, 6, 1),
        status=PredictionStatus.REFUTED,
    )
    prompt = build_faithfulness_prompt(
        "цей прогноз не справдився", [RetrievedPrediction(prediction=pred, distance=0.2, rank=1)]
    )
    assert "status" in prompt
    assert "АВТОРИТЕТ" in prompt  # інструкція: status — авторитетний вердикт


# --- citation-суддя (Task 10) ---


def _citation_source() -> RetrievedPrediction:
    prediction = Prediction(
        id="7c9f4e21-3a8b-4d15-9e02-6b1f8a4c7d33",
        document_id="d1",
        person_id="p1",
        claim_text="Росія розпадеться до 2024 року",
        prediction_date=date(2020, 8, 12),
        status=PredictionStatus.REFUTED,
    )
    return RetrievedPrediction(prediction=prediction, distance=0.1, rank=1)


def test_parse_citation_response_reads_verdict():
    supported, reason = parse_citation_response('{"supported": false, "reason": "інша тема"}')

    assert supported is False
    assert reason == "інша тема"


def test_parse_citation_response_survives_code_fence():
    supported, _ = parse_citation_response('```json\n{"supported": true, "reason": ""}\n```')

    assert supported is True


def test_citation_prompt_contains_sentence_and_source():
    prompt = build_citation_prompt("Речення [1].", _citation_source())

    assert "Речення [1]." in prompt
    assert "Росія розпадеться до 2024 року" in prompt


def test_parse_citation_response_ignores_trailing_prose():
    raw = '{"supported": true, "reason": "збіг"}\n\nПояснюю: джерело прямо містить це твердження.'

    supported, reason = parse_citation_response(raw)

    assert supported is True
    assert reason == "збіг"


def test_parse_faithfulness_response_ignores_trailing_prose():
    raw = '{"claims": [{"claim": "к", "supported": true, "reason": ""}]}\n\nКоментар судді.'

    claims = parse_faithfulness_response(raw)

    assert len(claims) == 1
