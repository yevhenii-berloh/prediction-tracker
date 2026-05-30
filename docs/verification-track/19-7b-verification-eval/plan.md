# Task 19.7b — Verification Model Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standalone eval script `scripts/verification_eval.py` що прогонить V2 verification prompt через 9 моделей проти 32 gold predictions, генерує per-model output files + aggregated metrics + markdown report з 4-step decision framework → production verifier model decision.

**Architecture:** Один script з 4 layers: (1) pure aggregation functions (testable), (2) decision framework (testable), (3) per-model file IO (testable), (4) async pipeline orchestration з sequential per-model loop + throttle. Tests — лише pure functions; pipeline — operational smoke через mock LLM.

**Tech Stack:** Python 3.12, asyncio, LiteLLM (via LLMClient), pytest, argparse. Working dir: `/Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker`. Use `.venv/bin/python`. Ukrainian commit messages.

**Spec:** [`design.md`](design.md)

**Baseline:** 154 tests pass. Target: ~166 (+12 нових pure-function tests).

---

## File Structure

| File | Role | LOC est. |
|---|---|---|
| `scripts/verification_eval.py` | NEW. Single script з усіма layers (constants, pure funcs, decision framework, IO, pipeline, CLI) | ~380 |
| `tests/test_verification_eval.py` | NEW. Pure aggregation + decision framework + IO helper tests | ~200 |
| `scripts/outputs/verification_eval/per_model/<provider>_<model>.json` | Stage 1 artifacts (gitignored) | — |
| `scripts/outputs/verification_eval/verification_eval_metrics.json` | Stage 2 aggregated (gitignored) | — |
| `scripts/outputs/verification_eval/verification_eval_report.md` | Stage 2 report (gitignored) | — |

`scripts/outputs/verification_eval/` уже існує і gitignored. `per_model/` створюватиметься у Stage 1.

---

## Constants block (top of `scripts/verification_eval.py`)

Цей блок shared у всіх Tasks — кожен Task додає до файла, цей constants block — частина Task 1 Step 3 (perший implementation step).

```python
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from evaluate_detection import (
    PROVIDER_API_KEY_ENV,
    CONCURRENCY_OVERRIDES,
    MIN_CALL_INTERVAL_SECONDS,
)
from prophet_checker.llm.client import LLMClient
from prophet_checker.llm.prompts import (
    build_verification_prompt_v2,
    get_verification_system_v2,
    parse_verification_response_v2,
)

logger = logging.getLogger(__name__)

MODELS = [
    "anthropic/claude-haiku-4-5",
    "openai/gpt-5-mini",
    "gemini/gemini-3.1-flash-lite-preview",
    "deepseek/deepseek-chat",
    "groq/llama-3.3-70b-versatile",
    "gemini/gemini-2.5-pro",
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-opus-4-6",
    "openai/gpt-5",
]

COST_PER_CALL_USD = {
    "anthropic/claude-haiku-4-5": 0.001,
    "openai/gpt-5-mini": 0.001,
    "gemini/gemini-3.1-flash-lite-preview": 0.0001,
    "deepseek/deepseek-chat": 0.0003,
    "groq/llama-3.3-70b-versatile": 0.0,
    "gemini/gemini-2.5-pro": 0.003,
    "anthropic/claude-sonnet-4-6": 0.012,
    "anthropic/claude-opus-4-6": 0.025,
    "openai/gpt-5": 0.010,
}

STATUS_LABELS = ("confirmed", "refuted", "unresolved", "premature")
STRENGTH_LABELS = ("low", "medium")
VALUE_LABELS = ("low", "medium", "high")

DEFAULT_GOLD_PATH = PROJECT_ROOT / "scripts" / "data" / "verification_gold_labels.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "scripts" / "outputs" / "verification_eval"
PER_MODEL_SUBDIR = "per_model"
METRICS_FILENAME = "verification_eval_metrics.json"
REPORT_FILENAME = "verification_eval_report.md"

BLOCKER_REJECT_RATE = 0.10
BLOCKER_MIN_STATUS_ACC = 0.5
QUALITY_TIER_TOLERANCE = 0.1
```

---

## Task 1: Pure aggregation functions

**Files:**
- Create: `scripts/verification_eval.py`
- Create: `tests/test_verification_eval.py`

### Step 1: Створити skeleton script з constants block

Створити `scripts/verification_eval.py` з повним constants block (showed above у "Constants block" section). Файл наразі не має жодних функцій — тільки imports + constants.

### Step 2: Написати failing test для compute_accuracy

Створити `tests/test_verification_eval.py`:

```python
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
```

### Step 3: Run tests — verify FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -v
```

Expected: 3 FAIL з `ImportError: cannot import name 'compute_accuracy'`

### Step 4: Implement compute_accuracy

Append до `scripts/verification_eval.py` (після constants block):

```python
def compute_accuracy(pairs: list[tuple]) -> float:
    if not pairs:
        return 0.0
    correct = sum(1 for gold, pred in pairs if gold == pred)
    return correct / len(pairs)
```

### Step 5: Run tests — verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -v
```

Expected: 3 PASS

### Step 6: Написати failing tests для confusion matrix

Append до `tests/test_verification_eval.py`:

```python
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
```

### Step 7: Run tests — verify FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k confusion_matrix -v
```

Expected: 3 FAIL

### Step 8: Implement compute_confusion_matrix

Append до `scripts/verification_eval.py`:

```python
def compute_confusion_matrix(pairs: list[tuple], labels: tuple[str, ...]) -> dict[str, dict[str, int]]:
    matrix = {gold: {pred: 0 for pred in labels} for gold in labels}
    for gold, pred in pairs:
        if gold in matrix and pred in matrix[gold]:
            matrix[gold][pred] += 1
    return matrix
```

### Step 9: Run tests — verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -v
```

Expected: 6 PASS

### Step 10: Написати failing tests для calibration_stats

Append до `tests/test_verification_eval.py`:

```python
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
```

### Step 11: Run tests — verify FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k calibration -v
```

Expected: 3 FAIL

### Step 12: Implement calibration_stats

Append до `scripts/verification_eval.py`:

```python
def calibration_stats(items: list[dict]) -> dict:
    correct = [i["confidence"] for i in items if i.get("is_correct")]
    wrong = [i["confidence"] for i in items if i.get("is_correct") is False]
    mean_correct = sum(correct) / len(correct) if correct else None
    mean_wrong = sum(wrong) / len(wrong) if wrong else None
    gap = (mean_correct - mean_wrong) if (mean_correct is not None and mean_wrong is not None) else None
    return {
        "mean_conf_correct": mean_correct,
        "mean_conf_wrong": mean_wrong,
        "gap": gap,
    }
```

### Step 13: Full suite check

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `163 passed` (154 baseline + 9 нових)

### Step 14: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/verification_eval.py tests/test_verification_eval.py && git commit -m "feat(scripts): verification_eval.py pure aggregation funcs (accuracy, confusion, calibration)"
```

---

## Task 2: Decision Framework

**Files:**
- Modify: `scripts/verification_eval.py` (append decision framework funcs)
- Modify: `tests/test_verification_eval.py` (append tests)

### Step 1: Написати failing test для filter_blockers

Append до `tests/test_verification_eval.py`:

```python
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
```

### Step 2: Run tests — verify FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k filter_blockers -v
```

Expected: 2 FAIL

### Step 3: Implement filter_blockers

Append до `scripts/verification_eval.py`:

```python
def filter_blockers(per_model: dict) -> tuple[dict, list[dict]]:
    survivors = {}
    filtered = []
    for model, metrics in per_model.items():
        reject = metrics.get("parser_reject_rate", 0.0)
        status_acc = metrics.get("status", {}).get("accuracy", 0.0)
        if reject > BLOCKER_REJECT_RATE:
            filtered.append({"model": model, "reason": f"parser_reject_rate={reject:.3f} > {BLOCKER_REJECT_RATE:.2f}"})
            continue
        if status_acc < BLOCKER_MIN_STATUS_ACC:
            filtered.append({"model": model, "reason": f"status_accuracy={status_acc:.3f} < {BLOCKER_MIN_STATUS_ACC}"})
            continue
        survivors[model] = metrics
    return survivors, filtered
```

### Step 4: Run tests — verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k filter_blockers -v
```

Expected: 2 PASS

### Step 5: Написати failing test для find_quality_tier

Append до `tests/test_verification_eval.py`:

```python
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
```

### Step 6: Run tests — verify FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k find_quality_tier -v
```

Expected: 3 FAIL

### Step 7: Implement find_quality_tier

Append до `scripts/verification_eval.py`:

```python
def find_quality_tier(per_model: dict) -> tuple[list[str], float]:
    if not per_model:
        return [], 0.0
    accs = {m: metrics["status"]["accuracy"] for m, metrics in per_model.items()}
    max_acc = max(accs.values())
    threshold = max_acc - QUALITY_TIER_TOLERANCE
    tier = [m for m, acc in accs.items() if acc >= threshold]
    return tier, max_acc
```

### Step 8: Run tests — verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k find_quality_tier -v
```

Expected: 3 PASS

### Step 9: Написати failing test для tie_break_within_tier

Append до `tests/test_verification_eval.py`:

```python
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
```

### Step 10: Run tests — verify FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k tie_break -v
```

Expected: 4 FAIL

### Step 11: Implement tie_break_within_tier

Append до `scripts/verification_eval.py`:

```python
def tie_break_within_tier(tier: list[str], per_model: dict) -> str | None:
    if not tier:
        return None

    def sort_key(model: str) -> tuple:
        m = per_model[model]
        cost = m.get("cost_total_usd", 0.0)
        latency = m.get("latency_mean_seconds", 0.0)
        sv_sum = m.get("prediction_strength", {}).get("accuracy", 0.0) + m.get("prediction_value", {}).get("accuracy", 0.0)
        return (cost, latency, -sv_sum)

    return sorted(tier, key=sort_key)[0]
```

### Step 12: Run tests — verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k tie_break -v
```

Expected: 4 PASS

### Step 13: Написати failing test для apply_decision_framework

Append до `tests/test_verification_eval.py`:

```python
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
```

### Step 14: Run tests — verify FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k apply_decision_framework -v
```

Expected: 2 FAIL

### Step 15: Implement apply_decision_framework

Append до `scripts/verification_eval.py`:

```python
def apply_decision_framework(per_model: dict) -> dict:
    survivors, filtered = filter_blockers(per_model)
    if not survivors:
        return {
            "step1_filtered_out": filtered,
            "step2_max_status_acc": 0.0,
            "step2_quality_tier": [],
            "step3_winner": None,
            "step3_rationale": "no models passed blocker filter",
        }
    tier, max_acc = find_quality_tier(survivors)
    winner = tie_break_within_tier(tier, survivors)
    if winner is None:
        rationale = "no winner — empty quality tier"
    else:
        w = survivors[winner]
        rationale = f"Tier-1 winner: lowest cost (${w['cost_total_usd']:.2f}), latency {w['latency_mean_seconds']:.2f}s"
    return {
        "step1_filtered_out": filtered,
        "step2_max_status_acc": max_acc,
        "step2_quality_tier": tier,
        "step3_winner": winner,
        "step3_rationale": rationale,
    }
```

### Step 16: Run tests — verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -v 2>&1 | tail -25
```

Expected: усі tests pass

### Step 17: Full suite check

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `174 passed` (163 після Task 1 + 11 нових = 174)

### Step 18: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/verification_eval.py tests/test_verification_eval.py && git commit -m "feat(scripts): decision framework (filter_blockers, find_quality_tier, tie_break, apply)"
```

---

## Task 3: Per-model file IO

**Files:**
- Modify: `scripts/verification_eval.py` (append IO helpers)
- Modify: `tests/test_verification_eval.py` (append tests)

### Step 1: Написати failing test для filename_for_model

Append до `tests/test_verification_eval.py`:

```python
def test_filename_for_model_replaces_slash():
    from verification_eval import filename_for_model
    assert filename_for_model("anthropic/claude-sonnet-4-6") == "anthropic_claude-sonnet-4-6.json"


def test_filename_for_model_preserves_dashes_and_dots():
    from verification_eval import filename_for_model
    assert filename_for_model("gemini/gemini-3.1-flash-lite-preview") == "gemini_gemini-3.1-flash-lite-preview.json"
```

### Step 2: Run tests — verify FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k filename_for_model -v
```

Expected: 2 FAIL

### Step 3: Implement filename_for_model

Append до `scripts/verification_eval.py`:

```python
def filename_for_model(model_id: str) -> str:
    safe = model_id.replace("/", "_")
    return f"{safe}.json"
```

### Step 4: Run tests — verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k filename_for_model -v
```

Expected: 2 PASS

### Step 5: Написати failing test для list_existing_per_model_files

Append до `tests/test_verification_eval.py`:

```python
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
```

### Step 6: Run tests — verify FAIL

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k list_existing_per_model_files -v
```

Expected: 3 FAIL

### Step 7: Implement list_existing_per_model_files

Append до `scripts/verification_eval.py`:

```python
def list_existing_per_model_files(per_model_dir: Path) -> list[str]:
    if not per_model_dir.exists():
        return []
    found = []
    for path in per_model_dir.glob("*.json"):
        stem = path.stem
        if "_" not in stem:
            continue
        provider, _, model = stem.partition("_")
        if provider in PROVIDER_API_KEY_ENV:
            found.append(f"{provider}/{model}")
    return found
```

### Step 8: Run tests — verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k list_existing_per_model_files -v
```

Expected: 3 PASS

### Step 9: Full suite check

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `179 passed`

### Step 10: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/verification_eval.py tests/test_verification_eval.py && git commit -m "feat(scripts): per-model file IO (filename, list existing)"
```

---

## Task 4: Stage 1 — pipeline runner

**Files:**
- Modify: `scripts/verification_eval.py` (append Stage 1 funcs)
- Modify: `tests/test_verification_eval.py` (append smoke test з mock LLM)

### Step 1: Implement build_llm_client (no test — direct util)

Append до `scripts/verification_eval.py`:

```python
def build_llm_client(model_id: str) -> LLMClient:
    if "/" not in model_id:
        raise ValueError(f"model_id must be 'provider/model', got {model_id!r}")
    provider, model = model_id.split("/", 1)
    env_var = PROVIDER_API_KEY_ENV.get(provider)
    if not env_var:
        raise ValueError(f"Unknown provider {provider!r}")
    api_key = os.environ.get(env_var)
    if not api_key:
        raise RuntimeError(f"Missing API key for {provider!r}: set {env_var}")
    return LLMClient(provider=provider, model=model, api_key=api_key, temperature=0.0)
```

### Step 2: Implement build_prompt_for_gold_entry (no test — composes existing funcs)

Append до `scripts/verification_eval.py`:

```python
def build_prompt_for_gold_entry(entry: dict, today: str) -> tuple[str, str]:
    prompt = build_verification_prompt_v2(
        claim=entry["claim_text"],
        prediction_date=entry["prediction_date"],
        target_date=entry["target_date"],
        today=today,
        situation=entry["situation"],
    )
    system = get_verification_system_v2(today=today)
    return prompt, system
```

### Step 3: Implement run_one_prediction async

Append до `scripts/verification_eval.py`:

```python
async def run_one_prediction(
    llm: LLMClient, entry: dict, today: str, model_id: str
) -> dict:
    prompt, system = build_prompt_for_gold_entry(entry, today)
    raw = None
    parsed = None
    parse_error = None
    start = monotonic()
    try:
        raw = await llm.complete(prompt, system=system)
        try:
            parsed = parse_verification_response_v2(raw)
        except (ValueError, json.JSONDecodeError) as e:
            parse_error = str(e)
    except Exception as e:
        parse_error = f"infra: {type(e).__name__}: {e}"
    latency = monotonic() - start
    return {
        "raw_response": raw,
        "parsed": parsed,
        "parse_error": parse_error,
        "latency_seconds": latency,
        "cost_usd": COST_PER_CALL_USD.get(model_id, 0.0),
    }
```

### Step 4: Implement run_for_model async

Append до `scripts/verification_eval.py`:

```python
async def run_for_model(
    model_id: str, gold_entries: list[dict], today: str, min_interval: float
) -> dict:
    llm = build_llm_client(model_id)
    results: dict[str, dict] = {}
    for i, entry in enumerate(gold_entries, 1):
        results[entry["id"]] = await run_one_prediction(llm, entry, today, model_id)
        print(f"  [{model_id}] {i}/{len(gold_entries)} done", flush=True)
        if min_interval > 0:
            await asyncio.sleep(min_interval)
    return {
        "metadata": {
            "model": model_id,
            "run_at": datetime.now(timezone.utc).isoformat(),
            "today": today,
            "n_predictions": len(gold_entries),
        },
        "results": results,
    }
```

### Step 5: Implement save_per_model_artifact

Append до `scripts/verification_eval.py`:

```python
def save_per_model_artifact(model_id: str, artifact: dict, per_model_dir: Path) -> Path:
    per_model_dir.mkdir(parents=True, exist_ok=True)
    out_path = per_model_dir / filename_for_model(model_id)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
```

### Step 6: Smoke test — mock LLM, single prediction, verify artifact written

Append до `tests/test_verification_eval.py`:

```python
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

    async def fake_build_client(model_id):
        return FakeLLM()

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
```

### Step 7: Run smoke test

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py::test_run_for_model_smoke_with_mock -v
```

Expected: PASS

### Step 8: Full suite check

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `180 passed`

### Step 9: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/verification_eval.py tests/test_verification_eval.py && git commit -m "feat(scripts): Stage 1 runner (build_llm_client, run_for_model, save artifact) + mock smoke"
```

---

## Task 5: Stage 2 — aggregation + report

**Files:**
- Modify: `scripts/verification_eval.py` (append Stage 2 funcs)
- Modify: `tests/test_verification_eval.py` (append render tests)

### Step 1: Implement metrics_for_model

Append до `scripts/verification_eval.py`:

```python
def metrics_for_model(per_model_artifact: dict, gold_index: dict[str, dict]) -> dict:
    results = per_model_artifact["results"]
    status_pairs: list[tuple] = []
    strength_pairs: list[tuple] = []
    value_pairs: list[tuple] = []
    calibration_items: list[dict] = []
    parser_rejects = 0
    cost_total = 0.0
    latencies: list[float] = []

    for entry_id, r in results.items():
        gold = gold_index.get(entry_id)
        if gold is None:
            continue
        cost_total += r.get("cost_usd", 0.0)
        latency = r.get("latency_seconds")
        if latency is not None:
            latencies.append(latency)
        if r.get("parse_error") is not None or r.get("parsed") is None:
            parser_rejects += 1
            continue
        parsed = r["parsed"]
        status_pairs.append((gold["expected_status"], parsed.get("status")))
        strength_pairs.append((gold["expected_strength"], parsed.get("prediction_strength")))
        value_pairs.append((gold["expected_value"], parsed.get("prediction_value")))
        is_correct = (gold["expected_status"] == parsed.get("status"))
        confidence = parsed.get("confidence")
        if confidence is not None:
            calibration_items.append({"confidence": confidence, "is_correct": is_correct})

    n_total = len(results)
    reject_rate = parser_rejects / n_total if n_total > 0 else 0.0
    return {
        "parsed_ok": len(status_pairs),
        "parser_rejects": parser_rejects,
        "parser_reject_rate": reject_rate,
        "status": {
            "accuracy": compute_accuracy(status_pairs),
            "confusion": compute_confusion_matrix(status_pairs, STATUS_LABELS),
        },
        "prediction_strength": {
            "accuracy": compute_accuracy(strength_pairs),
            "confusion": compute_confusion_matrix(strength_pairs, STRENGTH_LABELS),
        },
        "prediction_value": {
            "accuracy": compute_accuracy(value_pairs),
            "confusion": compute_confusion_matrix(value_pairs, VALUE_LABELS),
        },
        "calibration": calibration_stats(calibration_items),
        "cost_total_usd": cost_total,
        "latency_mean_seconds": (sum(latencies) / len(latencies)) if latencies else 0.0,
    }
```

### Step 2: Implement aggregate_all_models

Append до `scripts/verification_eval.py`:

```python
def load_gold(gold_path: Path) -> tuple[dict[str, dict], dict]:
    data = json.loads(gold_path.read_text(encoding="utf-8"))
    gold_index = {e["id"]: e for e in data["predictions"]}
    return gold_index, data["metadata"]


def aggregate_all_models(per_model_dir: Path, gold_index: dict) -> dict:
    per_model_metrics: dict[str, dict] = {}
    for path in sorted(per_model_dir.glob("*.json")):
        artifact = json.loads(path.read_text(encoding="utf-8"))
        model_id = artifact["metadata"]["model"]
        per_model_metrics[model_id] = metrics_for_model(artifact, gold_index)
    return per_model_metrics
```

### Step 3: Implement gather_disagreements (sanity check для report)

Append до `scripts/verification_eval.py`:

```python
def gather_disagreements(
    winner_model: str, per_model_dir: Path, gold_index: dict, limit: int = 5
) -> list[dict]:
    if winner_model is None:
        return []
    path = per_model_dir / filename_for_model(winner_model)
    if not path.exists():
        return []
    artifact = json.loads(path.read_text(encoding="utf-8"))
    disagreements: list[dict] = []
    for entry_id, r in artifact["results"].items():
        gold = gold_index.get(entry_id)
        if gold is None or r.get("parsed") is None:
            continue
        gold_status = gold["expected_status"]
        model_status = r["parsed"].get("status")
        if gold_status != model_status:
            disagreements.append({
                "id": entry_id,
                "gold_status": gold_status,
                "model_status": model_status,
                "claim": gold["claim_text"][:160],
            })
        if len(disagreements) >= limit:
            break
    return disagreements
```

### Step 4: Implement render_report

Append до `scripts/verification_eval.py`:

```python
def render_report(per_model: dict, decision: dict, disagreements: list[dict], total_cost: float) -> str:
    lines: list[str] = []
    lines.append("# Verification Model Evaluation Report")
    lines.append("")
    lines.append(f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    lines.append(f"**Models processed:** {len(per_model)}")
    lines.append(f"**Total cost:** ${total_cost:.2f}")
    lines.append("")
    lines.append(f"## Decision: PRODUCTION VERIFIER = `{decision['step3_winner']}`")
    lines.append("")
    lines.append(decision["step3_rationale"])
    lines.append("")
    if decision["step1_filtered_out"]:
        lines.append("**Filtered (blockers):**")
        for f in decision["step1_filtered_out"]:
            lines.append(f"- `{f['model']}` — {f['reason']}")
        lines.append("")
    lines.append("## Ranking")
    lines.append("")
    lines.append("| Model | Status acc | Strength acc | Value acc | Reject % | Cost | Latency |")
    lines.append("|---|---|---|---|---|---|---|")
    sorted_models = sorted(per_model.items(), key=lambda kv: kv[1]["status"]["accuracy"], reverse=True)
    for model, m in sorted_models:
        marker = " (WINNER)" if model == decision["step3_winner"] else ""
        lines.append(
            f"| `{model}`{marker} "
            f"| {m['status']['accuracy']:.3f} "
            f"| {m['prediction_strength']['accuracy']:.3f} "
            f"| {m['prediction_value']['accuracy']:.3f} "
            f"| {m['parser_reject_rate']*100:.1f}% "
            f"| ${m['cost_total_usd']:.3f} "
            f"| {m['latency_mean_seconds']:.2f}s |"
        )
    lines.append("")
    if disagreements:
        lines.append("## Sanity check: 5 disagreements (winner vs gold)")
        lines.append("")
        for i, d in enumerate(disagreements, 1):
            lines.append(f"{i}. `{d['id']}`")
            lines.append(f"   - Gold status: `{d['gold_status']}`")
            lines.append(f"   - Model status: `{d['model_status']}`")
            lines.append(f"   - Claim: {d['claim']}")
            lines.append("")
    return "\n".join(lines)
```

### Step 5: Implement run_aggregation (Stage 2 driver)

Append до `scripts/verification_eval.py`:

```python
def run_aggregation(output_dir: Path, gold_path: Path) -> None:
    per_model_dir = output_dir / PER_MODEL_SUBDIR
    gold_index, gold_metadata = load_gold(gold_path)
    per_model = aggregate_all_models(per_model_dir, gold_index)
    decision = apply_decision_framework(per_model)
    total_cost = sum(m["cost_total_usd"] for m in per_model.values())
    disagreements = gather_disagreements(decision["step3_winner"], per_model_dir, gold_index)
    metrics_artifact = {
        "metadata": {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "n_gold": len(gold_index),
            "n_models_processed": len(per_model),
        },
        "per_model": per_model,
        "decision_framework": {
            **decision,
            "step4_disagreements_for_review": disagreements,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / METRICS_FILENAME).write_text(
        json.dumps(metrics_artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / REPORT_FILENAME).write_text(
        render_report(per_model, decision, disagreements, total_cost), encoding="utf-8"
    )
    print(f"\nDECISION: {decision['step3_winner']}")
    print(f"RATIONALE: {decision['step3_rationale']}")
```

### Step 6: Написати failing test для render_report (smoke)

Append до `tests/test_verification_eval.py`:

```python
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
    assert "0.86" in md  # opus accuracy
    assert "0.81" in md  # sonnet accuracy
    assert "Total cost:** $0.65" in md
```

### Step 7: Run test — verify PASS

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/test_verification_eval.py -k render_report -v
```

Expected: PASS

### Step 8: Full suite check

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `181 passed`

### Step 9: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/verification_eval.py tests/test_verification_eval.py && git commit -m "feat(scripts): Stage 2 aggregation + render_report (markdown)"
```

---

## Task 6: CLI wiring

**Files:**
- Modify: `scripts/verification_eval.py` (append CLI)

### Step 1: Implement resolve_models_to_run

Append до `scripts/verification_eval.py`:

```python
def resolve_models_to_run(
    requested: list[str], skip_existing: bool, force: bool, per_model_dir: Path
) -> list[str]:
    if skip_existing and force:
        raise ValueError("--skip-existing and --force are mutually exclusive")
    existing = set(list_existing_per_model_files(per_model_dir)) if skip_existing else set()
    return [m for m in requested if m not in existing]


def parse_models_arg(arg: str | None) -> list[str]:
    if not arg:
        return list(MODELS)
    return [m.strip() for m in arg.split(",") if m.strip()]


def estimate_run_cost(models_to_run: list[str], n_predictions: int) -> float:
    return sum(COST_PER_CALL_USD.get(m, 0.0) * n_predictions for m in models_to_run)


def confirm_cost(estimate: float, n_calls: int, yes: bool) -> bool:
    print(f"\nPlan: {n_calls} calls, estimated cost ${estimate:.2f}")
    if yes:
        return True
    answer = input("Proceed? [y/N]: ").strip().lower()
    return answer == "y"
```

### Step 2: Implement run_stage1 async wrapper

Append до `scripts/verification_eval.py`:

```python
async def run_stage1(
    models: list[str], gold_entries: list[dict], today: str, output_dir: Path
) -> None:
    per_model_dir = output_dir / PER_MODEL_SUBDIR
    for model_id in models:
        min_interval = MIN_CALL_INTERVAL_SECONDS.get(model_id, 0.0)
        if min_interval > 0:
            est_min = len(gold_entries) * min_interval / 60
            print(f"  [{model_id}] throttle {min_interval}s/call → ~{est_min:.1f} min")
        print(f"\n=== {model_id} ===")
        artifact = await run_for_model(model_id, gold_entries, today, min_interval)
        path = save_per_model_artifact(model_id, artifact, per_model_dir)
        print(f"  Saved → {path}")
```

### Step 3: Implement main CLI

Append до `scripts/verification_eval.py`:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description="Verification model evaluation (Task 19.7b)")
    parser.add_argument("--model", default=None, help="comma-separated model ids (default: all)")
    parser.add_argument("--skip-existing", action="store_true", help="skip models that already have output file")
    parser.add_argument("--force", action="store_true", help="overwrite existing output (re-run model)")
    parser.add_argument("--aggregate-only", action="store_true", help="skip Stage 1, only Stage 2")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--yes", action="store_true", help="skip cost confirm prompt")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    gold_index, gold_metadata = load_gold(args.gold)
    today = gold_metadata.get("today", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    gold_entries = list(gold_index.values())

    if not args.aggregate_only:
        requested = parse_models_arg(args.model)
        per_model_dir = args.output_dir / PER_MODEL_SUBDIR
        models_to_run = resolve_models_to_run(requested, args.skip_existing, args.force, per_model_dir)
        if not models_to_run:
            print("No models to run (all skipped). Use --force to override.")
        else:
            estimate = estimate_run_cost(models_to_run, len(gold_entries))
            n_calls = len(models_to_run) * len(gold_entries)
            if not confirm_cost(estimate, n_calls, args.yes):
                print("Aborted.")
                return
            asyncio.run(run_stage1(models_to_run, gold_entries, today, args.output_dir))

    print("\nRunning aggregation...")
    run_aggregation(args.output_dir, args.gold)


if __name__ == "__main__":
    main()
```

### Step 4: Smoke — --help works

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/verification_eval.py --help
```

Expected: argparse help text з усіма прапорами `--model`, `--skip-existing`, `--force`, `--aggregate-only`, `--output-dir`, `--gold`, `--yes`.

### Step 5: Smoke — aggregate-only з порожнім dir не падає

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && mkdir -p /tmp/v19_7b_smoke/per_model && .venv/bin/python scripts/verification_eval.py --aggregate-only --output-dir /tmp/v19_7b_smoke 2>&1 | tail -5
```

Expected: завершується без помилок, виводить `DECISION: None` (бо нема per-model files), пише metrics.json і report.md у /tmp/v19_7b_smoke.

### Step 6: Cleanup smoke artifacts

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && rm -rf /tmp/v19_7b_smoke
```

### Step 7: Full suite check

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `181 passed`

### Step 8: Commit

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/verification_eval.py && git commit -m "feat(scripts): verification_eval CLI (model, skip-existing, force, aggregate-only, yes)"
```

---

## Task 7: Optional — ensure throttle entries для нових моделей

**Files:**
- Modify (maybe): `scripts/evaluate_detection.py`

Перевірка: чи мають `gemini/gemini-2.5-pro`, `openai/gpt-5`, `anthropic/claude-opus-4-6` entries у `CONCURRENCY_OVERRIDES` і `MIN_CALL_INTERVAL_SECONDS`. Якщо нема — додати safe defaults.

### Step 1: Перевірити existing entries

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "
from evaluate_detection import CONCURRENCY_OVERRIDES, MIN_CALL_INTERVAL_SECONDS
for m in ('gemini/gemini-2.5-pro', 'openai/gpt-5', 'anthropic/claude-opus-4-6'):
    print(f'{m}: conc={CONCURRENCY_OVERRIDES.get(m)}, interval={MIN_CALL_INTERVAL_SECONDS.get(m)}')
"
```

Якщо `None` для всіх трьох — додати entries у Step 2. Якщо вже є (наприклад через extraction_quality_eval.py setdefault) — пропустити цей Task entirely (commit не потрібен).

### Step 2 (conditional): Додати entries

Якщо Step 1 показав `None` для якихось моделей — у `scripts/evaluate_detection.py`, після existing `CONCURRENCY_OVERRIDES = {...}` блоку, додати:

```python
CONCURRENCY_OVERRIDES.setdefault("anthropic/claude-opus-4-6", 1)
MIN_CALL_INTERVAL_SECONDS.setdefault("anthropic/claude-opus-4-6", 8.0)
CONCURRENCY_OVERRIDES.setdefault("gemini/gemini-2.5-pro", 2)
MIN_CALL_INTERVAL_SECONDS.setdefault("gemini/gemini-2.5-pro", 1.0)
CONCURRENCY_OVERRIDES.setdefault("openai/gpt-5", 5)
MIN_CALL_INTERVAL_SECONDS.setdefault("openai/gpt-5", 0.0)
```

### Step 3: Verify

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "
from evaluate_detection import CONCURRENCY_OVERRIDES, MIN_CALL_INTERVAL_SECONDS
for m in ('gemini/gemini-2.5-pro', 'openai/gpt-5', 'anthropic/claude-opus-4-6'):
    print(f'{m}: conc={CONCURRENCY_OVERRIDES.get(m)}, interval={MIN_CALL_INTERVAL_SECONDS.get(m)}')
"
```

Expected: всі моделі мають числові values (not None).

### Step 4: Full suite check

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `181 passed`

### Step 5 (conditional): Commit

Якщо Step 2 змінив файл:

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git add scripts/evaluate_detection.py && git commit -m "chore(scripts): throttle entries для нових моделей 19.7b eval"
```

---

## Task 8: Final verification

**Files:** none (verification only)

### Step 1: Full suite final

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: `181 passed` (154 baseline + 27 нових)

### Step 2: Git log — 6 нових commits expected

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && git log --oneline | head -8
```

Expected (most recent 6 у топі):
- `feat(scripts): verification_eval CLI (model, skip-existing, force, aggregate-only, yes)`
- `feat(scripts): Stage 2 aggregation + render_report (markdown)`
- `feat(scripts): Stage 1 runner (build_llm_client, run_for_model, save artifact) + mock smoke`
- `feat(scripts): per-model file IO (filename, list existing)`
- `feat(scripts): decision framework (filter_blockers, find_quality_tier, tie_break, apply)`
- `feat(scripts): verification_eval.py pure aggregation funcs (accuracy, confusion, calibration)`
- (Task 7 conditional)

### Step 3: Smoke — імпорти і constants

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -c "
import sys
sys.path.insert(0, 'scripts')
from verification_eval import (
    MODELS, COST_PER_CALL_USD, STATUS_LABELS, STRENGTH_LABELS, VALUE_LABELS,
    compute_accuracy, compute_confusion_matrix, calibration_stats,
    filter_blockers, find_quality_tier, tie_break_within_tier, apply_decision_framework,
    filename_for_model, list_existing_per_model_files,
    build_llm_client, build_prompt_for_gold_entry, run_for_model, save_per_model_artifact,
    metrics_for_model, aggregate_all_models, gather_disagreements, render_report, run_aggregation,
)
print('All exports OK')
print('MODELS:', len(MODELS))
print('STATUS_LABELS:', STATUS_LABELS)
print('STRENGTH_LABELS:', STRENGTH_LABELS)
print('VALUE_LABELS:', VALUE_LABELS)
"
```

Expected: `All exports OK` + counts/labels.

### Step 4: Smoke — --help

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python scripts/verification_eval.py --help
```

Expected: argparse help з усіма 7 прапорами.

---

## Done criteria

- ✅ 181 tests pass (154 baseline + 27 нових)
- ✅ 6 commits (feat scope) + optional 7-й (chore throttle entries)
- ✅ scripts/verification_eval.py exists з 9-model MODELS list
- ✅ Decision framework testable (filter/tier/tie-break/apply)
- ✅ Per-model file IO (filename helper + glob aggregator)
- ✅ Stage 1 runner з mock smoke pass
- ✅ Stage 2 aggregation + markdown report
- ✅ CLI з усіма прапорами + cost confirm prompt

---

## Caveats для implementer

1. **Тести pure-only.** Stage 1 testується mock LLM (`FakeLLM`). Real API calls — operational (Task 19.7b execution phase), не unit test.

2. **`scripts/verification_eval.py` — один великий файл (~380 рядків).** Pet project pattern (`evaluate_detection.py`, `extraction_quality_eval.py` теж великі). НЕ splitти у модулі без потреби.

3. **`COST_PER_CALL_USD` — approximation, not exact billing.** Per-call USD естiмат базований на orientation prompt ~2k tokens + ~500 output. Точна вартість залежить від tokenizer. Для production decision це достатньо.

4. **`list_existing_per_model_files` filters by `PROVIDER_API_KEY_ENV`** (тільки відомі providers). Файли з іменами що НЕ матчать `provider_model.json` шаблон ігноруються.

5. **`build_llm_client` не cached** — у `run_for_model` створюється раз per model run. Sequential per-model, no contention.

6. **Tests test_verification_eval.py використовує tmp_path fixture** для IO tests — isolated, не залишає side-effect файлів.

7. **Stage 2 reads whatever per-model files exist.** Якщо одна модель не run — report показує тільки інші. Це features (incremental runs).

8. **Real eval run** (всі 9 моделей × 32 predictions = 288 calls, ~$1.70, ~25-40 хв wall) — окрема operational step **поза цим планом**. Запускається через `scripts/verification_eval.py` після того як план landed.
