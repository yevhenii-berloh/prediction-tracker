import json
from prophet_checker.llm.prompts import (
    build_extraction_prompt,
    build_rag_prompt,
    parse_extraction_response,
)


def test_build_extraction_prompt():
    prompt = build_extraction_prompt(
        text="Контрнаступ почнеться влітку 2023 року",
        person_name="Арестович",
        published_date="2023-01-15",
    )
    assert "Арестович" in prompt
    assert "Контрнаступ почнеться влітку 2023 року" in prompt
    assert "2023-01-15" in prompt
    assert "JSON" in prompt


def test_parse_extraction_response_valid():
    response = json.dumps({
        "predictions": [
            {
                "claim_text": "Контрнаступ почнеться влітку 2023",
                "prediction_date": "2023-01-15",
                "target_date": "2023-06-01",
                "topic": "війна",
            }
        ]
    })
    predictions = parse_extraction_response(response)
    assert len(predictions) == 1
    assert predictions[0]["claim_text"] == "Контрнаступ почнеться влітку 2023"


def test_parse_extraction_response_no_predictions():
    response = json.dumps({"predictions": []})
    predictions = parse_extraction_response(response)
    assert predictions == []


def test_parse_extraction_response_invalid_json():
    predictions = parse_extraction_response("not json at all")
    assert predictions == []


def test_parse_extraction_response_strips_markdown_code_fence():
    """LLMs often wrap JSON in ```json ... ``` fences — parser must handle it."""
    response = '```json\n{"predictions": [{"claim_text": "test", "prediction_date": "2024-01-01", "target_date": null, "topic": "війна"}]}\n```'
    predictions = parse_extraction_response(response)
    assert len(predictions) == 1
    assert predictions[0]["claim_text"] == "test"


def test_parse_extraction_response_strips_bare_code_fence():
    """Some models use ``` without language tag — must also be stripped."""
    response = '```\n{"predictions": [{"claim_text": "bare fence", "prediction_date": "2024-01-01", "target_date": null, "topic": ""}]}\n```'
    predictions = parse_extraction_response(response)
    assert len(predictions) == 1
    assert predictions[0]["claim_text"] == "bare fence"


def test_parse_extraction_response_handles_leading_trailing_whitespace():
    """Model may add blank lines around JSON — parser must tolerate."""
    response = '\n\n  {"predictions": [{"claim_text": "test", "prediction_date": "2024-01-01", "target_date": null, "topic": ""}]}  \n\n'
    predictions = parse_extraction_response(response)
    assert len(predictions) == 1


def test_build_rag_prompt():
    predictions_context = [
        {"claim_text": "Pred 1", "status": "confirmed", "confidence": 0.9},
        {"claim_text": "Pred 2", "status": "refuted", "confidence": 0.7},
    ]
    prompt = build_rag_prompt(
        question="Що казав Арестович про контрнаступ?",
        predictions_context=predictions_context,
    )
    assert "Що казав Арестович про контрнаступ?" in prompt
    assert "Pred 1" in prompt
    assert "Pred 2" in prompt
