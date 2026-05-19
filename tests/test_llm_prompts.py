import json
import pytest
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


def test_build_verification_prompt_v2_substitutes_all_fields():
    from prophet_checker.llm.prompts import build_verification_prompt_v2

    prompt = build_verification_prompt_v2(
        claim="Test claim",
        prediction_date="2024-01-01",
        target_date="2024-12-31",
        today="2025-01-15",
        post_excerpt="Original post text",
    )
    assert "Test claim" in prompt
    assert "2024-01-01" in prompt
    assert "2024-12-31" in prompt
    assert "2025-01-15" in prompt
    assert "Original post text" in prompt


def test_parse_verification_response_v2_terminal_confirmed():
    from prophet_checker.llm.prompts import parse_verification_response_v2

    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "high",
        "prediction_value": "high",
        "reasoning": "Event occurred as predicted in June 2023.",
        "evidence": "Counteroffensive started June 2023 per Reuters.",
        "retry_after": null,
        "max_horizon": null
    }"""
    result = parse_verification_response_v2(response)
    assert result["status"] == "confirmed"
    assert result["prediction_strength"] == "high"
    assert result["prediction_value"] == "high"
    assert result["evidence"] == "Counteroffensive started June 2023 per Reuters."


def test_parse_verification_response_v2_premature():
    from prophet_checker.llm.prompts import parse_verification_response_v2

    response = """{
        "status": "premature",
        "confidence": 0.5,
        "prediction_strength": "medium",
        "prediction_value": "medium",
        "reasoning": "Trump's term started recently — too early to assess.",
        "evidence": null,
        "retry_after": "2025-06-01",
        "max_horizon": "2028-01-01"
    }"""
    result = parse_verification_response_v2(response)
    assert result["status"] == "premature"
    assert result["retry_after"] == "2025-06-01"
    assert result["max_horizon"] == "2028-01-01"


def test_parse_v2_raises_on_invalid_json():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    with pytest.raises(json.JSONDecodeError):
        parse_verification_response_v2("not valid json")


def test_parse_v2_raises_on_missing_required_field():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = '{"status": "confirmed", "confidence": 0.9, "evidence": "fact"}'
    with pytest.raises(ValueError, match="missing required field"):
        parse_verification_response_v2(response)


def test_parse_v2_raises_on_invalid_status():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "verified",
        "confidence": 0.9,
        "prediction_strength": "high",
        "prediction_value": "high",
        "reasoning": "...",
        "evidence": "fact"
    }"""
    with pytest.raises(ValueError, match="invalid status"):
        parse_verification_response_v2(response)


def test_parse_v2_raises_on_invalid_prediction_strength():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "strong",
        "prediction_value": "high",
        "reasoning": "...",
        "evidence": "fact"
    }"""
    with pytest.raises(ValueError, match="invalid prediction_strength"):
        parse_verification_response_v2(response)


def test_parse_v2_raises_on_invalid_prediction_value():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "high",
        "prediction_value": "huge",
        "reasoning": "...",
        "evidence": "fact"
    }"""
    with pytest.raises(ValueError, match="invalid prediction_value"):
        parse_verification_response_v2(response)


def test_parse_v2_raises_premature_without_retry_after():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "premature",
        "confidence": 0.5,
        "prediction_strength": "medium",
        "prediction_value": "medium",
        "reasoning": "...",
        "evidence": null,
        "retry_after": null
    }"""
    with pytest.raises(ValueError, match="premature requires retry_after"):
        parse_verification_response_v2(response)


def test_parse_v2_raises_terminal_without_evidence():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "high",
        "prediction_value": "high",
        "reasoning": "...",
        "evidence": null
    }"""
    with pytest.raises(ValueError, match="confirmed requires evidence"):
        parse_verification_response_v2(response)


def test_parse_v2_drops_extraneous_retry_after_on_terminal():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "high",
        "prediction_value": "high",
        "reasoning": "Event occurred.",
        "evidence": "concrete fact",
        "retry_after": "2025-06-01"
    }"""
    result = parse_verification_response_v2(response)
    assert result["status"] == "confirmed"
    assert result["retry_after"] is None
    assert result["evidence"] == "concrete fact"


def test_parse_v2_drops_extraneous_retry_after_on_unresolved():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "unresolved",
        "confidence": 0.4,
        "prediction_strength": "low",
        "prediction_value": "low",
        "reasoning": "Too vague.",
        "evidence": null,
        "retry_after": "2025-06-01"
    }"""
    result = parse_verification_response_v2(response)
    assert result["status"] == "unresolved"
    assert result["retry_after"] is None


def test_parse_v2_drops_extraneous_max_horizon_on_non_premature():
    from prophet_checker.llm.prompts import parse_verification_response_v2
    response = """{
        "status": "confirmed",
        "confidence": 0.9,
        "prediction_strength": "high",
        "prediction_value": "high",
        "reasoning": "Event occurred.",
        "evidence": "concrete fact",
        "max_horizon": "2028-01-01"
    }"""
    result = parse_verification_response_v2(response)
    assert result["status"] == "confirmed"
    assert result["max_horizon"] is None


def test_validate_context_in_post_success():
    from prophet_checker.llm.prompts import validate_context_in_post
    post = "Сьогодні я думаю що війна закінчиться скоро. Це моя думка."
    ctx = "війна закінчиться скоро"
    assert validate_context_in_post(ctx, post) is True


def test_validate_context_in_post_normalizes_whitespace():
    from prophet_checker.llm.prompts import validate_context_in_post
    post = "Перше речення.\n\n   Друге  речення\tз багатьма пробілами."
    ctx = "Друге речення з багатьма пробілами"
    assert validate_context_in_post(ctx, post) is True


def test_validate_context_in_post_fails_on_hallucination():
    from prophet_checker.llm.prompts import validate_context_in_post
    post = "Реальний текст посту про економіку."
    ctx = "Цей текст модель вигадала і його у пості немає"
    assert validate_context_in_post(ctx, post) is False


def test_validate_context_in_post_rejects_empty_or_whitespace():
    from prophet_checker.llm.prompts import validate_context_in_post
    post = "Реальний текст посту."
    assert validate_context_in_post("", post) is False
    assert validate_context_in_post("   \n\t  ", post) is False
    assert validate_context_in_post("Реальний", "") is False
