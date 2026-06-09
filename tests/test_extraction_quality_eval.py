"""TDD tests for Task 13.5 — Extraction Quality Evaluation pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from extraction.judge_prompts import (
    VERDICT_VALUES,
    VERDICT_ORDINAL,
    JUDGE_SYSTEM,
    build_judge_prompt,
)


# =============================================================================
# Group A1 — VERDICT enum + ordinal mapping
# =============================================================================


def test_verdict_values_are_six():
    """Six categorical verdicts per design spec."""
    assert set(VERDICT_VALUES) == {
        "exact_match",
        "faithful_paraphrase",
        "valid_but_metadata_error",
        "not_a_prediction",
        "truncated",
        "hallucination",
    }


def test_verdict_ordinal_mapping_matches_spec():
    """Ordinal scoring per design table — 0/1/2/3 per spec."""
    assert VERDICT_ORDINAL == {
        "exact_match": 3,
        "faithful_paraphrase": 3,
        "valid_but_metadata_error": 2,
        "not_a_prediction": 1,
        "truncated": 1,
        "hallucination": 0,
    }


def test_judge_system_contains_four_criteria_and_seven_categories():
    """JUDGE_SYSTEM must reference 4-criteria YES test and 7-category NO rubric (A-G)."""
    sys_lower = JUDGE_SYSTEM.lower()
    assert "four criteria" in sys_lower
    assert "future" in sys_lower
    assert "verifiable" in sys_lower
    assert "substantive" in sys_lower  # criterion 4 added 2026-04-21
    # All 6 verdict labels mentioned in instructions
    for verdict in VERDICT_VALUES:
        assert verdict in JUDGE_SYSTEM


def test_build_judge_prompt_includes_post_text_and_claims():
    """build_judge_prompt formats post + claims into reviewer's user message."""
    post_text = "Контрнаступ почнеться влітку 2023."
    claims = [
        {
            "claim_text": "Контрнаступ почнеться влітку 2023",
            "prediction_date": "2023-01-15",
            "target_date": "2023-06-01",
            "topic": "війна",
        }
    ]
    prompt = build_judge_prompt(
        post_text=post_text, published_date="2023-01-15", extracted_claims=claims
    )
    assert post_text in prompt
    assert "Контрнаступ почнеться влітку 2023" in prompt
    assert "2023-01-15" in prompt  # published date
    assert "війна" in prompt


def test_build_judge_prompt_handles_empty_claims():
    """When extractor returned empty list, prompt must explicitly say so."""
    prompt = build_judge_prompt(
        post_text="some post", published_date="2024-01-01", extracted_claims=[]
    )
    assert "no claims" in prompt.lower() or "empty" in prompt.lower() or "0" in prompt


# =============================================================================
# Group A2 — parse_judge_response
# =============================================================================


from extraction.judge_prompts import parse_judge_response


def test_parse_judge_response_valid_json():
    """Standard well-formed JSON response is parsed into structured dict."""
    response = json.dumps(
        {
            "per_claim": [
                {
                    "claim_text": "X buy Y",
                    "verdict": "faithful_paraphrase",
                    "reasoning": "Captures the prediction",
                }
            ],
            "missed_predictions": [
                {"text_excerpt": "Z will fall", "why_valid": "Concrete event"}
            ],
        }
    )
    parsed = parse_judge_response(response)
    assert len(parsed["per_claim"]) == 1
    assert parsed["per_claim"][0]["verdict"] == "faithful_paraphrase"
    assert len(parsed["missed_predictions"]) == 1


def test_parse_judge_response_strips_markdown_fence():
    """Like extractor parser, must tolerate ```json...``` wrappers."""
    response = (
        "```json\n"
        + json.dumps({"per_claim": [], "missed_predictions": []})
        + "\n```"
    )
    parsed = parse_judge_response(response)
    assert parsed["per_claim"] == []
    assert parsed["missed_predictions"] == []


def test_parse_judge_response_invalid_json_returns_error_marker():
    """Malformed JSON is reported as parse error, not raised."""
    parsed = parse_judge_response("not json at all")
    assert parsed["per_claim"] == []
    assert parsed["missed_predictions"] == []
    assert parsed.get("parse_error") is not None


def test_parse_judge_response_unknown_verdict_falls_back_to_marker():
    """Verdict outside the 6 allowed values is preserved but flagged."""
    response = json.dumps(
        {
            "per_claim": [
                {
                    "claim_text": "X",
                    "verdict": "totally_made_up",
                    "reasoning": "...",
                }
            ],
            "missed_predictions": [],
        }
    )
    parsed = parse_judge_response(response)
    assert parsed["per_claim"][0]["verdict"] == "totally_made_up"
    assert parsed["per_claim"][0].get("verdict_invalid") is True


def test_parse_judge_response_missing_top_level_keys_defaults_empty():
    """If response only has per_claim, missed_predictions defaults to []."""
    response = json.dumps(
        {
            "per_claim": [
                {"claim_text": "X", "verdict": "exact_match", "reasoning": "..."}
            ]
        }
    )
    parsed = parse_judge_response(response)
    assert parsed["missed_predictions"] == []
    assert len(parsed["per_claim"]) == 1


def test_parse_judge_response_handles_trailing_data():
    """Opus 4.6 sometimes emits trailing text or a 2nd JSON object after the
    main response. raw_decode reads only the first valid JSON and ignores rest.
    Without this fix, ~10% of Opus calls fail with 'Extra data' JSONDecodeError.
    """
    primary = json.dumps(
        {
            "per_claim": [
                {"claim_text": "X", "verdict": "exact_match", "reasoning": "ok"}
            ],
            "missed_predictions": [],
        }
    )
    # Append a trailing explanation block (mimics observed Opus output)
    response = primary + "\n\nNote: I judged this confidently."
    parsed = parse_judge_response(response)
    assert parsed["parse_error"] is None
    assert len(parsed["per_claim"]) == 1
    assert parsed["per_claim"][0]["verdict"] == "exact_match"


def test_parse_judge_response_handles_leading_preamble():
    """Opus 4.6 occasionally adds a preamble like 'Here is my evaluation:' before
    the JSON. parse strips leading non-JSON text by finding the first `{`.
    """
    primary = json.dumps(
        {
            "per_claim": [
                {"claim_text": "Y", "verdict": "faithful_paraphrase", "reasoning": "ok"}
            ],
            "missed_predictions": [],
        }
    )
    response = "Here is my evaluation of the extracted claims:\n\n" + primary
    parsed = parse_judge_response(response)
    assert parsed["parse_error"] is None
    assert len(parsed["per_claim"]) == 1
    assert parsed["per_claim"][0]["verdict"] == "faithful_paraphrase"


# =============================================================================
# Group A3 — aggregate_metrics
# =============================================================================


from extraction.extraction_quality_eval import aggregate_metrics


def _gold(yes_ids, no_ids):
    """Helper to build gold-label list."""
    return [{"id": i, "has_prediction": True} for i in yes_ids] + [
        {"id": i, "has_prediction": False} for i in no_ids
    ]


def test_aggregate_metrics_empty_judgements():
    """No judgements → empty per_model section, no errors."""
    report = aggregate_metrics(judgements={}, gold_labels=_gold(["a"], ["b"]))
    assert report["per_model"] == {}


def test_aggregate_metrics_verdict_distribution_and_avg_score():
    """Single model with known verdict mix produces correct distribution + avg ordinal."""
    judgements = {
        "model_x": {
            "post_1": {
                "per_claim": [
                    {"verdict": "exact_match"},
                    {"verdict": "hallucination"},
                ],
                "missed_predictions": [],
            },
            "post_2": {
                "per_claim": [{"verdict": "faithful_paraphrase"}],
                "missed_predictions": [],
            },
        }
    }
    report = aggregate_metrics(
        judgements=judgements,
        gold_labels=_gold(["post_1", "post_2"], []),
    )
    m = report["per_model"]["model_x"]
    assert m["total_claims"] == 3
    assert m["verdict_distribution"]["exact_match"] == 1
    assert m["verdict_distribution"]["faithful_paraphrase"] == 1
    assert m["verdict_distribution"]["hallucination"] == 1
    # Ordinal sum: 3 + 0 + 3 = 6 over 3 claims = 2.0
    assert m["avg_quality_score"] == pytest.approx(2.0, abs=1e-6)
    assert m["hallucination_rate"] == pytest.approx(1 / 3, abs=1e-6)


def test_aggregate_metrics_missed_predictions_counted():
    """missed_predictions across posts contribute to missed_rate vs gold_YES count."""
    judgements = {
        "model_x": {
            "post_1": {
                "per_claim": [{"verdict": "exact_match"}],
                "missed_predictions": [{"text_excerpt": "X", "why_valid": "..."}],
            },
            "post_2": {
                "per_claim": [],
                "missed_predictions": [
                    {"text_excerpt": "Y", "why_valid": "..."},
                    {"text_excerpt": "Z", "why_valid": "..."},
                ],
            },
        }
    }
    report = aggregate_metrics(
        judgements=judgements, gold_labels=_gold(["post_1", "post_2"], [])
    )
    m = report["per_model"]["model_x"]
    assert m["missed_predictions_count"] == 3


def test_aggregate_metrics_gold_agreement_matrix():
    """Cross-tab judge verdicts vs gold labels."""
    judgements = {
        "model_x": {
            # Gold YES + has valid extraction → agreement
            "yes_with_valid": {
                "per_claim": [{"verdict": "faithful_paraphrase"}],
                "missed_predictions": [],
            },
            # Gold YES but no valid extraction → disagreement
            "yes_no_valid": {
                "per_claim": [{"verdict": "hallucination"}],
                "missed_predictions": [],
            },
            # Gold NO but has extraction labeled valid → disagreement (FP)
            "no_with_valid": {
                "per_claim": [{"verdict": "exact_match"}],
                "missed_predictions": [],
            },
            # Gold NO + no valid extractions → agreement
            "no_no_valid": {
                "per_claim": [{"verdict": "not_a_prediction"}],
                "missed_predictions": [],
            },
        }
    }
    report = aggregate_metrics(
        judgements=judgements,
        gold_labels=_gold(
            ["yes_with_valid", "yes_no_valid"],
            ["no_with_valid", "no_no_valid"],
        ),
    )
    matrix = report["per_model"]["model_x"]["gold_agreement"]
    assert matrix["gold_YES_with_valid_extraction"] == 1
    assert matrix["gold_YES_no_valid_extraction"] == 1
    assert matrix["gold_NO_with_extractions_labeled_valid"] == 1
    assert matrix["gold_NO_without_valid_extractions"] == 1


def test_aggregate_metrics_handles_invalid_verdict():
    """Verdict marked verdict_invalid is counted but excluded from ordinal mean."""
    judgements = {
        "model_x": {
            "post_1": {
                "per_claim": [
                    {"verdict": "exact_match"},
                    {"verdict": "totally_made_up", "verdict_invalid": True},
                ],
                "missed_predictions": [],
            }
        }
    }
    report = aggregate_metrics(
        judgements=judgements, gold_labels=_gold(["post_1"], [])
    )
    m = report["per_model"]["model_x"]
    assert m["total_claims"] == 2
    assert m["invalid_verdict_count"] == 1
    # avg only over valid verdicts: 3.0 / 1 = 3.0
    assert m["avg_quality_score"] == pytest.approx(3.0, abs=1e-6)


def test_aggregate_metrics_handles_parse_error():
    """Posts with parse_error must be counted but excluded from gold_agreement.

    A judge parse-failure is an INFRA issue, not a model failure. We should
    track count for visibility but NOT penalize the model in gold_agreement
    matrix (treat as missing data).
    """
    judgements = {
        "model_x": {
            # Successful judgement — gold_YES, valid extraction → counted
            "yes_ok": {
                "per_claim": [{"verdict": "exact_match"}],
                "missed_predictions": [],
                "parse_error": None,
            },
            # Judge parse failed on gold_YES post — should NOT be counted
            # as gold_YES_no_valid_extraction (since we don't actually know).
            "yes_parse_failed": {
                "per_claim": [],
                "missed_predictions": [],
                "parse_error": "JSONDecodeError: line 1 column 5",
            },
            # Judge parse failed on gold_NO post — also excluded.
            "no_parse_failed": {
                "per_claim": [],
                "missed_predictions": [],
                "parse_error": "TypeError: unexpected token",
            },
        }
    }
    report = aggregate_metrics(
        judgements=judgements,
        gold_labels=_gold(["yes_ok", "yes_parse_failed"], ["no_parse_failed"]),
    )
    m = report["per_model"]["model_x"]
    assert m["parse_error_count"] == 2
    matrix = m["gold_agreement"]
    # Only "yes_ok" contributes — parse-error posts excluded
    assert matrix["gold_YES_with_valid_extraction"] == 1
    assert matrix["gold_YES_no_valid_extraction"] == 0
    assert matrix["gold_NO_with_extractions_labeled_valid"] == 0
    assert matrix["gold_NO_without_valid_extractions"] == 0


# =============================================================================
# Group B1 — Stage 1 orchestration (mocked extractor)
# =============================================================================


import asyncio
from datetime import date

from extraction.extraction_quality_eval import run_stage1_extraction


def _fake_pred(claim: str, topic: str = "війна") -> MagicMock:
    """Create a Prediction-like object with the fields Stage 1 reads."""
    p = MagicMock()
    p.claim_text = claim
    p.prediction_date = date(2024, 1, 15)
    p.target_date = date(2024, 6, 1)
    p.topic = topic
    return p


def _make_factory(claim_map: dict[str, dict[str, list[str]]]):
    """Build extractor_factory that returns mock extractors emitting fixed claims per post.

    claim_map: {extractor_id: {post_id: [claim_text, ...]}}
    """

    def factory(model_id: str):
        extractor = MagicMock()

        async def fake_extract(*, document_id, **kwargs):
            claims = claim_map.get(model_id, {}).get(document_id, [])
            return [_fake_pred(c) for c in claims]

        extractor.extract = AsyncMock(side_effect=fake_extract)
        return extractor

    return factory


async def test_stage1_invokes_each_extractor_per_post(tmp_path):
    posts = [
        {"id": "p1", "person_name": "Арестович", "published_at": "2024-01-01", "text": "T1"},
        {"id": "p2", "person_name": "Арестович", "published_at": "2024-01-02", "text": "T2"},
    ]
    claim_map = {
        "model_a": {"p1": ["claim_a1"], "p2": ["claim_a2_1", "claim_a2_2"]},
        "model_b": {"p1": ["claim_b1"], "p2": []},
    }
    out_path = tmp_path / "extractions.json"

    await run_stage1_extraction(
        extractors=["model_a", "model_b"],
        posts=posts,
        author_filter="Арестович",
        output_path=out_path,
        extractor_factory=_make_factory(claim_map),
    )

    saved = json.loads(out_path.read_text())
    assert "extractions" in saved
    assert set(saved["extractions"].keys()) == {"model_a", "model_b"}
    assert len(saved["extractions"]["model_a"]["p1"]) == 1
    assert saved["extractions"]["model_a"]["p1"][0]["claim_text"] == "claim_a1"
    assert len(saved["extractions"]["model_a"]["p2"]) == 2
    assert saved["extractions"]["model_b"]["p2"] == []


async def test_stage1_filters_posts_by_author(tmp_path):
    posts = [
        {"id": "p1", "person_name": "Арестович", "published_at": "2024-01-01", "text": "T1"},
        {"id": "p2", "person_name": "Гордон", "published_at": "2024-01-02", "text": "T2"},
    ]
    claim_map = {"model_a": {"p1": ["c1"], "p2": ["c2"]}}
    out_path = tmp_path / "extractions.json"

    await run_stage1_extraction(
        extractors=["model_a"],
        posts=posts,
        author_filter="Арестович",
        output_path=out_path,
        extractor_factory=_make_factory(claim_map),
    )

    saved = json.loads(out_path.read_text())
    # p2 (Гордон) excluded
    assert set(saved["extractions"]["model_a"].keys()) == {"p1"}


async def test_stage1_handles_extractor_exception_as_empty_list(tmp_path):
    posts = [
        {"id": "p1", "person_name": "Арестович", "published_at": "2024-01-01", "text": "T"}
    ]
    out_path = tmp_path / "extractions.json"

    def factory(model_id):
        m = MagicMock()
        m.extract = AsyncMock(side_effect=RuntimeError("API down"))
        return m

    await run_stage1_extraction(
        extractors=["model_a"],
        posts=posts,
        author_filter="Арестович",
        output_path=out_path,
        extractor_factory=factory,
    )

    saved = json.loads(out_path.read_text())
    # Errors logged separately, post key still present with empty claims
    assert saved["extractions"]["model_a"]["p1"] == []
    assert "p1" in saved["errors"]["model_a"]


# =============================================================================
# Group B2 — Stage 2 orchestration (mocked judge LLM)
# =============================================================================


from extraction.extraction_quality_eval import run_stage2_judge


def _make_judge_factory(response_map: dict[str, str]):
    """Factory that returns judge LLM whose .complete() returns canned response.

    response_map: {keyword_in_prompt: judge_response_text}
    """

    def factory(judge_id: str):
        client = MagicMock()

        async def fake_complete(prompt: str, system: str | None = None):
            # naive lookup: find first response_map key present in prompt
            for key, resp in response_map.items():
                if key in prompt:
                    return resp
            return json.dumps({"per_claim": [], "missed_predictions": []})

        client.complete = AsyncMock(side_effect=fake_complete)
        return client

    return factory


async def test_stage2_invokes_judge_per_extractor_post(tmp_path):
    extractions_artifact = {
        "metadata": {"extractors": ["model_a", "model_b"]},
        "extractions": {
            "model_a": {
                "p1": [
                    {
                        "claim_text": "claim_a1",
                        "prediction_date": "2024-01-01",
                        "target_date": None,
                        "topic": "війна",
                    }
                ],
            },
            "model_b": {"p1": []},
        },
        "errors": {"model_a": {}, "model_b": {}},
    }
    extractions_path = tmp_path / "extractions.json"
    extractions_path.write_text(
        json.dumps(extractions_artifact, ensure_ascii=False)
    )
    posts = [
        {
            "id": "p1",
            "person_name": "Арестович",
            "published_at": "2024-01-01",
            "text": "Some Ukrainian post text",
        }
    ]
    out_path = tmp_path / "judgements.json"

    response_map = {
        "claim_a1": json.dumps(
            {
                "per_claim": [
                    {
                        "claim_text": "claim_a1",
                        "verdict": "exact_match",
                        "reasoning": "ok",
                    }
                ],
                "missed_predictions": [],
            }
        ),
    }

    await run_stage2_judge(
        judge_model="judge/test",
        extractions_path=extractions_path,
        posts=posts,
        output_path=out_path,
        judge_factory=_make_judge_factory(response_map),
    )

    saved = json.loads(out_path.read_text())
    assert (
        saved["judgements"]["model_a"]["p1"]["per_claim"][0]["verdict"]
        == "exact_match"
    )
    # Empty extractions still get judged (returns empty per_claim)
    assert saved["judgements"]["model_b"]["p1"]["per_claim"] == []


async def test_stage2_skips_post_with_extraction_error(tmp_path):
    extractions_artifact = {
        "metadata": {"extractors": ["model_a"]},
        "extractions": {"model_a": {"p1": [], "p2": []}},
        "errors": {"model_a": {"p1": "RuntimeError: API down"}},
    }
    extractions_path = tmp_path / "extractions.json"
    extractions_path.write_text(json.dumps(extractions_artifact))
    posts = [
        {"id": "p1", "person_name": "Арестович", "published_at": "2024-01-01", "text": "T"},
        {"id": "p2", "person_name": "Арестович", "published_at": "2024-01-02", "text": "T"},
    ]
    out_path = tmp_path / "judgements.json"

    judge_factory = _make_judge_factory({})

    await run_stage2_judge(
        judge_model="judge/test",
        extractions_path=extractions_path,
        posts=posts,
        output_path=out_path,
        judge_factory=judge_factory,
    )

    saved = json.loads(out_path.read_text())
    # p1 was an error in Stage 1 — judge skips, marker present
    assert (
        saved["judgements"]["model_a"]["p1"].get("skipped_due_to_extraction_error")
        is True
    )
    # p2 had empty extractions but no error → judge was called
    assert "skipped_due_to_extraction_error" not in saved["judgements"]["model_a"]["p2"]


async def test_stage2_handles_judge_parse_failure(tmp_path):
    extractions_artifact = {
        "metadata": {"extractors": ["model_a"]},
        "extractions": {
            "model_a": {
                "p1": [
                    {
                        "claim_text": "UNIQUE_MARKER_CLAIM",
                        "prediction_date": None,
                        "target_date": None,
                        "topic": "",
                    }
                ]
            }
        },
        "errors": {"model_a": {}},
    }
    extractions_path = tmp_path / "extractions.json"
    extractions_path.write_text(json.dumps(extractions_artifact))
    posts = [
        {"id": "p1", "person_name": "Арестович", "published_at": "2024-01-01", "text": "T"}
    ]
    out_path = tmp_path / "judgements.json"

    # Match by claim text (which IS in the rendered judge prompt) so this
    # response is returned only for our specific post under test.
    response_map = {"UNIQUE_MARKER_CLAIM": "this is not valid JSON at all"}

    await run_stage2_judge(
        judge_model="judge/test",
        extractions_path=extractions_path,
        posts=posts,
        output_path=out_path,
        judge_factory=_make_judge_factory(response_map),
    )

    saved = json.loads(out_path.read_text())
    # parse_error preserved, per_claim empty so aggregate excludes from gold_agreement
    assert saved["judgements"]["model_a"]["p1"]["parse_error"] is not None
    assert saved["judgements"]["model_a"]["p1"]["per_claim"] == []


# =============================================================================
# Group B3 — Stage 3 orchestration (load + aggregate + save)
# =============================================================================


from extraction.extraction_quality_eval import run_stage3_aggregate


def test_stage3_aggregate_writes_report_with_per_model_section(tmp_path):
    judgements_artifact = {
        "metadata": {"judge": "j/test"},
        "judgements": {
            "model_a": {
                "p1": {
                    "per_claim": [{"verdict": "exact_match"}],
                    "missed_predictions": [],
                }
            }
        },
    }
    judgements_path = tmp_path / "judgements.json"
    judgements_path.write_text(json.dumps(judgements_artifact))

    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps([{"id": "p1", "has_prediction": True}]))

    output_path = tmp_path / "report.json"
    run_stage3_aggregate(
        judgements_path=judgements_path,
        gold_labels_path=gold_path,
        output_path=output_path,
    )

    report = json.loads(output_path.read_text())
    assert "per_model" in report
    assert report["per_model"]["model_a"]["total_claims"] == 1
    assert report["per_model"]["model_a"]["avg_quality_score"] == pytest.approx(3.0)


# =============================================================================
# Group C1 — CLI smoke
# =============================================================================


def test_cli_help_lists_stage_argument():
    """CLI --help mentions --stages, --judge, --extractors, --output-dir."""
    from extraction.extraction_quality_eval import _build_arg_parser

    parser = _build_arg_parser()
    out = parser.format_help()
    assert "--stages" in out
    assert "--judge" in out
    assert "--extractors" in out
    assert "--output-dir" in out


def test_cli_parses_stages_csv():
    from extraction.extraction_quality_eval import _build_arg_parser, _parse_stages

    args = _build_arg_parser().parse_args(["--stages", "1,3"])
    assert _parse_stages(args.stages) == {1, 3}


def test_cli_parses_extractors_csv():
    from extraction.extraction_quality_eval import _build_arg_parser

    args = _build_arg_parser().parse_args(
        ["--extractors", "gemini/x,deepseek/y"]
    )
    assert args.extractors == "gemini/x,deepseek/y"


# =============================================================================
# Group C2 — End-to-end pipeline (all 3 stages, mocked LLMs)
# =============================================================================


async def test_full_pipeline_synthetic_data(tmp_path):
    """Stage 1 → Stage 2 → Stage 3 with mocked extractor + judge."""
    posts = [
        {
            "id": "p1",
            "person_name": "Арестович",
            "published_at": "2024-01-01",
            "text": "Контрнаступ почнеться влітку 2023",
        },
        {
            "id": "p2",
            "person_name": "Арестович",
            "published_at": "2024-01-02",
            "text": "Сьогодні погода гарна",
        },
    ]
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            [
                {"id": "p1", "has_prediction": True},
                {"id": "p2", "has_prediction": False},
            ]
        )
    )

    extractions_path = tmp_path / "extraction_outputs.json"
    judgements_path = tmp_path / "extraction_judgements.json"
    report_path = tmp_path / "extraction_eval_report.json"

    # Mock extractor: extracts 1 claim from p1, none from p2
    claim_map = {
        "model_test": {"p1": ["Контрнаступ почнеться влітку 2023"], "p2": []},
    }
    await run_stage1_extraction(
        extractors=["model_test"],
        posts=posts,
        author_filter="Арестович",
        output_path=extractions_path,
        extractor_factory=_make_factory(claim_map),
    )

    # Mock judge: rates the claim as exact_match (matched by claim_text substring in prompt)
    judge_response = json.dumps(
        {
            "per_claim": [
                {
                    "claim_text": "Контрнаступ почнеться влітку 2023",
                    "verdict": "exact_match",
                    "reasoning": "Verbatim quote",
                }
            ],
            "missed_predictions": [],
        }
    )
    await run_stage2_judge(
        judge_model="judge/test",
        extractions_path=extractions_path,
        posts=posts,
        output_path=judgements_path,
        judge_factory=_make_judge_factory({"Контрнаступ": judge_response}),
    )

    report = run_stage3_aggregate(
        judgements_path=judgements_path,
        gold_labels_path=gold_path,
        output_path=report_path,
    )

    m = report["per_model"]["model_test"]
    assert m["total_claims"] == 1
    assert m["verdict_distribution"]["exact_match"] == 1
    assert m["avg_quality_score"] == pytest.approx(3.0)
    assert m["gold_agreement"]["gold_YES_with_valid_extraction"] == 1
    assert m["gold_agreement"]["gold_NO_without_valid_extractions"] == 1


async def test_re_run_stage_2_only_uses_existing_extractions(tmp_path):
    """Demonstrates artifact-based re-runs: Stage 1 once, Stage 2 multiple times."""
    posts = [
        {
            "id": "p1",
            "person_name": "Арестович",
            "published_at": "2024-01-01",
            "text": "T",
        }
    ]
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps([{"id": "p1", "has_prediction": True}]))
    extractions_path = tmp_path / "extraction_outputs.json"

    await run_stage1_extraction(
        extractors=["model_a"],
        posts=posts,
        author_filter="Арестович",
        output_path=extractions_path,
        extractor_factory=_make_factory({"model_a": {"p1": ["UNIQUE_CLAIM_X"]}}),
    )

    # Run Stage 2 with judge_v1
    judgements_v1 = tmp_path / "judgements_v1.json"
    await run_stage2_judge(
        judge_model="judge/v1",
        extractions_path=extractions_path,
        posts=posts,
        output_path=judgements_v1,
        judge_factory=_make_judge_factory(
            {
                "UNIQUE_CLAIM_X": json.dumps(
                    {
                        "per_claim": [
                            {
                                "claim_text": "UNIQUE_CLAIM_X",
                                "verdict": "exact_match",
                                "reasoning": "v1",
                            }
                        ],
                        "missed_predictions": [],
                    }
                )
            }
        ),
    )

    # Same Stage 1 artifact, different judge — Stage 1 NOT re-run
    judgements_v2 = tmp_path / "judgements_v2.json"
    await run_stage2_judge(
        judge_model="judge/v2",
        extractions_path=extractions_path,
        posts=posts,
        output_path=judgements_v2,
        judge_factory=_make_judge_factory(
            {
                "UNIQUE_CLAIM_X": json.dumps(
                    {
                        "per_claim": [
                            {
                                "claim_text": "UNIQUE_CLAIM_X",
                                "verdict": "hallucination",
                                "reasoning": "v2 disagrees",
                            }
                        ],
                        "missed_predictions": [],
                    }
                )
            }
        ),
    )

    j1 = json.loads(judgements_v1.read_text())
    j2 = json.loads(judgements_v2.read_text())
    assert (
        j1["judgements"]["model_a"]["p1"]["per_claim"][0]["verdict"]
        == "exact_match"
    )
    assert (
        j2["judgements"]["model_a"]["p1"]["per_claim"][0]["verdict"]
        == "hallucination"
    )


def test_aggregate_metrics_no_gold_nulls_gold_fields():
    from extraction.extraction_quality_eval import aggregate_metrics
    judgements = {
        "model_x": {
            "post_1": {
                "per_claim": [{"verdict": "exact_match"}, {"verdict": "hallucination"}],
                "missed_predictions": [{"text_excerpt": "X", "why_valid": "..."}],
            },
        }
    }
    m = aggregate_metrics(judgements=judgements, gold_labels=None)["per_model"]["model_x"]
    assert m["missed_rate"] is None
    assert m["gold_agreement"] is None
    assert m["total_claims"] == 2
    assert m["avg_quality_score"] == pytest.approx(1.5, abs=1e-6)
    assert m["hallucination_rate"] == pytest.approx(0.5, abs=1e-6)
    assert m["missed_predictions_count"] == 1


def test_aggregate_metrics_empty_gold_treated_as_no_gold():
    from extraction.extraction_quality_eval import aggregate_metrics
    judgements = {"model_x": {"p": {"per_claim": [{"verdict": "exact_match"}], "missed_predictions": []}}}
    m = aggregate_metrics(judgements=judgements, gold_labels=[])["per_model"]["model_x"]
    assert m["missed_rate"] is None
    assert m["gold_agreement"] is None
    assert m["total_claims"] == 1
