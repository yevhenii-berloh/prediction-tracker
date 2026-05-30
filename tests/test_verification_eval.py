import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def test_compute_accuracy_all_correct():
    from verification_eval import compute_accuracy
    pairs = [("a", "a"), ("b", "b"), ("c", "c")]
    assert compute_accuracy(pairs) == 1.0


def test_compute_accuracy_half_correct():
    from verification_eval import compute_accuracy
    pairs = [("a", "a"), ("b", "x"), ("c", "c"), ("d", "y")]
    assert compute_accuracy(pairs) == 0.5


def test_compute_accuracy_empty_returns_zero():
    from verification_eval import compute_accuracy
    assert compute_accuracy([]) == 0.0


def test_compute_confusion_matrix_status_4_classes():
    from verification_eval import compute_confusion_matrix, STATUS_LABELS
    pairs = [
        ("confirmed", "confirmed"),
        ("confirmed", "unresolved"),
        ("refuted", "refuted"),
        ("unresolved", "unresolved"),
        ("unresolved", "premature"),
        ("premature", "premature"),
        ("premature", "premature"),
    ]
    matrix = compute_confusion_matrix(pairs, STATUS_LABELS)
    assert matrix["confirmed"]["confirmed"] == 1
    assert matrix["confirmed"]["unresolved"] == 1
    assert matrix["refuted"]["refuted"] == 1
    assert matrix["unresolved"]["unresolved"] == 1
    assert matrix["unresolved"]["premature"] == 1
    assert matrix["premature"]["premature"] == 2
    assert matrix["confirmed"]["refuted"] == 0
    assert matrix["refuted"]["confirmed"] == 0


def test_compute_confusion_matrix_strength_2_classes():
    from verification_eval import compute_confusion_matrix, STRENGTH_LABELS
    pairs = [("low", "low"), ("low", "medium"), ("medium", "medium")]
    matrix = compute_confusion_matrix(pairs, STRENGTH_LABELS)
    assert matrix["low"]["low"] == 1
    assert matrix["low"]["medium"] == 1
    assert matrix["medium"]["medium"] == 1
    assert matrix["medium"]["low"] == 0


def test_compute_confusion_matrix_skips_out_of_label_pred():
    from verification_eval import compute_confusion_matrix, STRENGTH_LABELS
    pairs = [("low", "high"), ("medium", "high")]
    matrix = compute_confusion_matrix(pairs, STRENGTH_LABELS)
    assert matrix["low"]["low"] == 0
    assert matrix["low"]["medium"] == 0
    assert matrix["medium"]["low"] == 0
    assert matrix["medium"]["medium"] == 0


def test_calibration_stats_well_calibrated():
    from verification_eval import calibration_stats
    items = [
        {"confidence": 0.9, "is_correct": True},
        {"confidence": 0.85, "is_correct": True},
        {"confidence": 0.55, "is_correct": False},
        {"confidence": 0.6, "is_correct": False},
    ]
    stats = calibration_stats(items)
    assert stats["mean_conf_correct"] == 0.875
    assert stats["mean_conf_wrong"] == 0.575
    assert round(stats["gap"], 3) == 0.300


def test_calibration_stats_no_wrong():
    from verification_eval import calibration_stats
    items = [{"confidence": 0.9, "is_correct": True}]
    stats = calibration_stats(items)
    assert stats["mean_conf_correct"] == 0.9
    assert stats["mean_conf_wrong"] is None
    assert stats["gap"] is None


def test_calibration_stats_empty():
    from verification_eval import calibration_stats
    stats = calibration_stats([])
    assert stats == {"mean_conf_correct": None, "mean_conf_wrong": None, "gap": None}


def test_filter_blockers_drops_high_reject_rate():
    from verification_eval import filter_blockers
    per_model = {
        "good": {"parser_reject_rate": 0.0, "status": {"accuracy": 0.8}},
        "bad_reject": {"parser_reject_rate": 0.15, "status": {"accuracy": 0.8}},
    }
    survivors, filtered = filter_blockers(per_model)
    assert "good" in survivors
    assert "bad_reject" not in survivors
    assert filtered == [{"model": "bad_reject", "reason": "parser_reject_rate=0.150 > 0.10"}]


def test_filter_blockers_drops_low_accuracy():
    from verification_eval import filter_blockers
    per_model = {
        "good": {"parser_reject_rate": 0.0, "status": {"accuracy": 0.8}},
        "bad_acc": {"parser_reject_rate": 0.0, "status": {"accuracy": 0.4}},
    }
    survivors, filtered = filter_blockers(per_model)
    assert "good" in survivors
    assert "bad_acc" not in survivors
    assert filtered == [{"model": "bad_acc", "reason": "status_accuracy=0.400 < 0.5"}]


def test_find_quality_tier_top_minus_01():
    from verification_eval import find_quality_tier
    per_model = {
        "opus":   {"status": {"accuracy": 0.86}},
        "sonnet": {"status": {"accuracy": 0.83}},
        "gpt5":   {"status": {"accuracy": 0.80}},
        "haiku":  {"status": {"accuracy": 0.71}},
    }
    tier, max_acc = find_quality_tier(per_model)
    assert max_acc == 0.86
    assert set(tier) == {"opus", "sonnet", "gpt5"}
    assert "haiku" not in tier


def test_find_quality_tier_single_model():
    from verification_eval import find_quality_tier
    per_model = {"only": {"status": {"accuracy": 0.7}}}
    tier, max_acc = find_quality_tier(per_model)
    assert max_acc == 0.7
    assert tier == ["only"]


def test_find_quality_tier_empty():
    from verification_eval import find_quality_tier
    tier, max_acc = find_quality_tier({})
    assert tier == []
    assert max_acc == 0.0


def test_tie_break_picks_cheapest_in_tier():
    from verification_eval import tie_break_within_tier
    per_model = {
        "opus":   {"cost_total_usd": 0.50, "latency_mean_seconds": 4.0, "prediction_strength": {"accuracy": 0.7}, "prediction_value": {"accuracy": 0.6}},
        "sonnet": {"cost_total_usd": 0.15, "latency_mean_seconds": 2.8, "prediction_strength": {"accuracy": 0.7}, "prediction_value": {"accuracy": 0.6}},
        "gpt5":   {"cost_total_usd": 0.30, "latency_mean_seconds": 3.5, "prediction_strength": {"accuracy": 0.7}, "prediction_value": {"accuracy": 0.6}},
    }
    winner = tie_break_within_tier(["opus", "sonnet", "gpt5"], per_model)
    assert winner == "sonnet"


def test_tie_break_cost_tie_breaks_by_latency():
    from verification_eval import tie_break_within_tier
    per_model = {
        "a": {"cost_total_usd": 0.10, "latency_mean_seconds": 3.0, "prediction_strength": {"accuracy": 0.7}, "prediction_value": {"accuracy": 0.6}},
        "b": {"cost_total_usd": 0.10, "latency_mean_seconds": 2.0, "prediction_strength": {"accuracy": 0.7}, "prediction_value": {"accuracy": 0.6}},
    }
    assert tie_break_within_tier(["a", "b"], per_model) == "b"


def test_tie_break_cost_and_latency_tie_breaks_by_strength_plus_value():
    from verification_eval import tie_break_within_tier
    per_model = {
        "a": {"cost_total_usd": 0.10, "latency_mean_seconds": 2.0, "prediction_strength": {"accuracy": 0.6}, "prediction_value": {"accuracy": 0.5}},
        "b": {"cost_total_usd": 0.10, "latency_mean_seconds": 2.0, "prediction_strength": {"accuracy": 0.7}, "prediction_value": {"accuracy": 0.5}},
    }
    assert tie_break_within_tier(["a", "b"], per_model) == "b"


def test_tie_break_empty_tier():
    from verification_eval import tie_break_within_tier
    assert tie_break_within_tier([], {}) is None


def test_apply_decision_framework_picks_winner_end_to_end():
    from verification_eval import apply_decision_framework
    per_model = {
        "opus":    {"parser_reject_rate": 0.0,  "status": {"accuracy": 0.86}, "prediction_strength": {"accuracy": 0.7}, "prediction_value": {"accuracy": 0.6}, "cost_total_usd": 0.50, "latency_mean_seconds": 4.2},
        "sonnet":  {"parser_reject_rate": 0.0,  "status": {"accuracy": 0.83}, "prediction_strength": {"accuracy": 0.74}, "prediction_value": {"accuracy": 0.66}, "cost_total_usd": 0.15, "latency_mean_seconds": 2.8},
        "haiku":   {"parser_reject_rate": 0.0,  "status": {"accuracy": 0.71}, "prediction_strength": {"accuracy": 0.66}, "prediction_value": {"accuracy": 0.55}, "cost_total_usd": 0.03, "latency_mean_seconds": 1.8},
        "broken":  {"parser_reject_rate": 0.20, "status": {"accuracy": 0.60}, "prediction_strength": {"accuracy": 0.5},  "prediction_value": {"accuracy": 0.4},  "cost_total_usd": 0.01, "latency_mean_seconds": 5.0},
    }
    decision = apply_decision_framework(per_model)
    assert decision["step1_filtered_out"] == [{"model": "broken", "reason": "parser_reject_rate=0.200 > 0.10"}]
    assert decision["step2_max_status_acc"] == 0.86
    assert set(decision["step2_quality_tier"]) == {"opus", "sonnet"}
    assert decision["step3_winner"] == "sonnet"
    assert "Tier" in decision["step3_rationale"] or "tier" in decision["step3_rationale"]


def test_apply_decision_framework_all_filtered():
    from verification_eval import apply_decision_framework
    per_model = {
        "broken1": {"parser_reject_rate": 0.5, "status": {"accuracy": 0.3}},
        "broken2": {"parser_reject_rate": 0.0, "status": {"accuracy": 0.2}},
    }
    decision = apply_decision_framework(per_model)
    assert decision["step3_winner"] is None
    assert "no models passed" in decision["step3_rationale"].lower()


def test_filename_for_model_replaces_slash():
    from verification_eval import filename_for_model
    assert filename_for_model("anthropic/claude-sonnet-4-6") == "anthropic_claude-sonnet-4-6.json"


def test_filename_for_model_preserves_dashes_and_dots():
    from verification_eval import filename_for_model
    assert filename_for_model("gemini/gemini-3.1-flash-lite-preview") == "gemini_gemini-3.1-flash-lite-preview.json"


def test_list_existing_per_model_files(tmp_path):
    from verification_eval import list_existing_per_model_files
    (tmp_path / "anthropic_claude-haiku-4-5.json").write_text("{}")
    (tmp_path / "openai_gpt-5-mini.json").write_text("{}")
    (tmp_path / "not_a_model.txt").write_text("ignore")
    found = list_existing_per_model_files(tmp_path)
    assert set(found) == {"anthropic/claude-haiku-4-5", "openai/gpt-5-mini"}


def test_list_existing_per_model_files_empty_dir(tmp_path):
    from verification_eval import list_existing_per_model_files
    assert list_existing_per_model_files(tmp_path) == []


def test_list_existing_per_model_files_missing_dir(tmp_path):
    from verification_eval import list_existing_per_model_files
    missing = tmp_path / "does_not_exist"
    assert list_existing_per_model_files(missing) == []


import asyncio
import json as json_mod
from unittest.mock import AsyncMock, MagicMock


def test_run_for_model_smoke_with_mock(tmp_path):
    from verification_eval import run_for_model, save_per_model_artifact

    valid_response = json_mod.dumps({
        "status": "premature",
        "confidence": 0.5,
        "prediction_strength": "medium",
        "prediction_value": "medium",
        "reasoning": "test",
        "evidence": None,
        "retry_after": "2027-01-01",
        "max_horizon": "2030-01-01",
    })

    gold_entries = [
        {
            "id": "test:1",
            "claim_text": "Test claim",
            "situation": "Test situation",
            "prediction_date": "2024-01-01",
            "target_date": None,
        }
    ]

    class FakeLLM:
        async def complete(self, prompt, system):
            return valid_response

    import verification_eval
    original = verification_eval.build_llm_client
    verification_eval.build_llm_client = lambda mid: FakeLLM()
    try:
        artifact = asyncio.run(run_for_model("anthropic/claude-haiku-4-5", gold_entries, "2026-05-23", 0.0))
    finally:
        verification_eval.build_llm_client = original

    assert artifact["metadata"]["model"] == "anthropic/claude-haiku-4-5"
    assert artifact["metadata"]["n_predictions"] == 1
    assert "test:1" in artifact["results"]
    r = artifact["results"]["test:1"]
    assert r["parsed"]["status"] == "premature"
    assert r["parse_error"] is None
    assert r["latency_seconds"] >= 0
    assert r["cost_usd"] == 0.001

    saved = save_per_model_artifact("anthropic/claude-haiku-4-5", artifact, tmp_path)
    assert saved.name == "anthropic_claude-haiku-4-5.json"
    reloaded = json_mod.loads(saved.read_text())
    assert reloaded["metadata"]["model"] == "anthropic/claude-haiku-4-5"


def test_render_report_includes_winner_and_table():
    from verification_eval import render_report
    per_model = {
        "anthropic/claude-sonnet-4-6": {
            "status": {"accuracy": 0.81},
            "prediction_strength": {"accuracy": 0.74},
            "prediction_value": {"accuracy": 0.66},
            "parser_reject_rate": 0.0,
            "cost_total_usd": 0.15,
            "latency_mean_seconds": 2.8,
        },
        "anthropic/claude-opus-4-6": {
            "status": {"accuracy": 0.86},
            "prediction_strength": {"accuracy": 0.71},
            "prediction_value": {"accuracy": 0.69},
            "parser_reject_rate": 0.0,
            "cost_total_usd": 0.50,
            "latency_mean_seconds": 4.2,
        },
    }
    decision = {
        "step1_filtered_out": [],
        "step2_max_status_acc": 0.86,
        "step2_quality_tier": ["anthropic/claude-opus-4-6", "anthropic/claude-sonnet-4-6"],
        "step3_winner": "anthropic/claude-sonnet-4-6",
        "step3_rationale": "Tier-1 winner: lowest cost",
    }
    md = render_report(per_model, decision, [], 0.65)
    assert "PRODUCTION VERIFIER = `anthropic/claude-sonnet-4-6`" in md
    assert "Tier-1 winner" in md
    assert "(WINNER)" in md
    assert "0.86" in md
    assert "0.81" in md
    assert "Total cost:** $0.65" in md
