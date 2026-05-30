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


def find_quality_tier(per_model: dict) -> tuple[list[str], float]:
    if not per_model:
        return [], 0.0
    accs = {m: metrics["status"]["accuracy"] for m, metrics in per_model.items()}
    max_acc = max(accs.values())
    threshold = max_acc - QUALITY_TIER_TOLERANCE
    tier = [m for m, acc in accs.items() if acc >= threshold]
    return tier, max_acc


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


def filename_for_model(model_id: str) -> str:
    safe = model_id.replace("/", "_")
    return f"{safe}.json"


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


def save_per_model_artifact(model_id: str, artifact: dict, per_model_dir: Path) -> Path:
    per_model_dir.mkdir(parents=True, exist_ok=True)
    out_path = per_model_dir / filename_for_model(model_id)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


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
