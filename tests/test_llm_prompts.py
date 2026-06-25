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
    response = json.dumps(
        {
            "predictions": [
                {
                    "claim_text": "Контрнаступ почнеться влітку 2023",
                    "prediction_date": "2023-01-15",
                    "target_date": "2023-06-01",
                    "topic": "війна",
                }
            ]
        }
    )
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
    from datetime import date

    from prophet_checker.models.domain import Prediction, PredictionStatus, RetrievedPrediction

    sources = [
        RetrievedPrediction(
            prediction=Prediction(
                id="pred-1",
                document_id="d",
                person_id="x",
                claim_text="Контрнаступ не досягне моря",
                prediction_date=date(2023, 6, 1),
                status=PredictionStatus.REFUTED,
                confidence=0.7,
            ),
            distance=0.2,
            rank=1,
        )
    ]
    prompt = build_rag_prompt(question="Що казав про контрнаступ?", sources=sources)
    assert "Що казав про контрнаступ?" in prompt
    assert "Контрнаступ не досягне моря" in prompt
    assert "pred-1" in prompt
    assert "2023-06-01" in prompt
    assert "refuted" in prompt


def test_build_verification_prompt_v2_substitutes_all_fields():
    from prophet_checker.llm.prompts import build_verification_prompt_v2

    prompt = build_verification_prompt_v2(
        claim="Test claim",
        prediction_date="2024-01-01",
        target_date="2024-12-31",
        today="2025-01-15",
        situation="Original post text",
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


def test_validate_situation_accepts_non_empty():
    from prophet_checker.llm.prompts import validate_situation

    assert validate_situation("У відповідь на іранські погрози") is True


def test_validate_situation_rejects_empty_and_none():
    from prophet_checker.llm.prompts import validate_situation

    assert validate_situation("") is False
    assert validate_situation(None) is False


def test_validate_situation_rejects_whitespace_only():
    from prophet_checker.llm.prompts import validate_situation

    assert validate_situation("   \n\t  ") is False


def test_extraction_template_includes_situation_field():
    from prophet_checker.llm.prompts import EXTRACTION_TEMPLATE

    assert "situation: 1-2 sentences" in EXTRACTION_TEMPLATE
    assert '"situation": "..."' in EXTRACTION_TEMPLATE


def test_parse_extraction_response_extracts_situation():
    import json
    from prophet_checker.llm.prompts import parse_extraction_response

    response = json.dumps(
        {
            "predictions": [
                {
                    "claim_text": "Війна закінчиться у 2026",
                    "prediction_date": "2024-01-15",
                    "target_date": "2026-12-31",
                    "topic": "війна",
                    "situation": "Обговорення перспектив завершення війни у 2026",
                }
            ]
        }
    )
    predictions = parse_extraction_response(response)
    assert len(predictions) == 1
    assert predictions[0]["situation"] == "Обговорення перспектив завершення війни у 2026"


def test_build_verification_prompt_v2_accepts_situation_kwarg():
    import pytest
    from prophet_checker.llm.prompts import build_verification_prompt_v2

    prompt = build_verification_prompt_v2(
        claim="X",
        prediction_date="2024-01-01",
        target_date=None,
        today="2026-05-14",
        situation="Verbatim quote",
    )
    assert "Verbatim quote" in prompt

    with pytest.raises(TypeError):
        build_verification_prompt_v2(
            claim="X",
            prediction_date="2024-01-01",
            target_date=None,
            today="2026-05-14",
            context="should fail under new signature",
        )


def test_verdict_system_is_plain_v3():
    from prophet_checker.llm.prompts import get_verification_system_v2

    system = get_verification_system_v2(today="2026-05-23")
    assert "2026-05-23" in system
    assert "fact-checker" in system
    assert "concrete falsifiable claim with measurable outcome" in system
    assert "outcome reshapes a country, region, or balance of power" in system
    assert "high = RARE" not in system
    assert "VAGUENESS RULE" not in system


def test_get_assessment_system_v2_injects_today_and_markers():
    from prophet_checker.llm.prompts import get_assessment_system_v2

    system = get_assessment_system_v2(today="2026-05-23")
    assert "2026-05-23" in system
    assert "INDEPENDENT axes" in system
    assert "RARE" in system
    assert "fact-checker" not in system


def test_parse_assessment_happy_path():
    from prophet_checker.llm.prompts import parse_assessment_response_v2

    raw = json.dumps(
        {
            "reasoning": "Vague hedge.",
            "prediction_strength": "low",
            "prediction_value": "high",
        }
    )
    result = parse_assessment_response_v2(raw)
    assert result == {"prediction_strength": "low"}


def test_parse_assessment_strips_code_fence():
    from prophet_checker.llm.prompts import parse_assessment_response_v2

    raw = '```json\n{"prediction_strength": "medium", "prediction_value": "low"}\n```'
    assert parse_assessment_response_v2(raw) == {"prediction_strength": "medium"}


def test_parse_assessment_raises_on_missing_strength():
    from prophet_checker.llm.prompts import parse_assessment_response_v2

    raw = json.dumps({"prediction_value": "high"})
    with pytest.raises(ValueError, match="missing required field: prediction_strength"):
        parse_assessment_response_v2(raw)


def test_parse_assessment_raises_on_invalid_strength():
    from prophet_checker.llm.prompts import parse_assessment_response_v2

    raw = json.dumps({"prediction_strength": "strong", "prediction_value": "high"})
    with pytest.raises(ValueError, match="invalid prediction_strength"):
        parse_assessment_response_v2(raw)
