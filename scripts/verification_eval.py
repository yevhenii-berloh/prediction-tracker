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


def compute_accuracy(pairs: list[tuple]) -> float:
    if not pairs:
        return 0.0
    correct = sum(1 for gold, pred in pairs if gold == pred)
    return correct / len(pairs)


def compute_confusion_matrix(pairs: list[tuple], labels: tuple[str, ...]) -> dict[str, dict[str, int]]:
    matrix = {gold: {pred: 0 for pred in labels} for gold in labels}
    for gold, pred in pairs:
        if gold in matrix and pred in matrix[gold]:
            matrix[gold][pred] += 1
    return matrix


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
