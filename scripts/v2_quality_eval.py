from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from extraction_judge_prompts import (
    JUDGE_SYSTEM,
    VERDICT_ORDINAL,
    VERDICT_VALUES,
    build_judge_prompt,
    parse_judge_response,
)
from evaluate_detection import PROVIDER_API_KEY_ENV
from extraction_quality_eval import aggregate_metrics
from prophet_checker.llm.client import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_JUDGE = "anthropic/claude-opus-4-6"
DEFAULT_MODEL_LABEL = "gemini-flash-lite-v2"
JUDGE_MIN_INTERVAL = 8.0
V1_REPORT_PATH = PROJECT_ROOT / "scripts" / "outputs" / "extraction_eval" / "extraction_eval_report.json"
DEFAULT_V2_EXTRACTIONS = PROJECT_ROOT / "scripts" / "outputs" / "verification_eval" / "v2_extraction_outputs.json"
DEFAULT_JUDGEMENTS_OUT = PROJECT_ROOT / "scripts" / "outputs" / "verification_eval" / "v2_judgements.json"
DEFAULT_REPORT_OUT = PROJECT_ROOT / "scripts" / "outputs" / "verification_eval" / "v2_quality_eval_report.md"
GOLD_LABELS_PATH = PROJECT_ROOT / "scripts" / "data" / "gold_labels.json"


def build_judge_client(model_id: str) -> LLMClient:
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


async def judge_post(
    judge: LLMClient, post_text: str, published_date: str, claims: list[dict]
) -> dict:
    if not claims:
        return {"per_claim": [], "missed_predictions": []}
    prompt = build_judge_prompt(
        post_text=post_text, published_date=published_date, extracted_claims=claims,
    )
    try:
        raw = await judge.complete(prompt, system=JUDGE_SYSTEM)
    except Exception as e:
        logger.exception("Judge call failed")
        return {
            "judge_error": f"{type(e).__name__}: {e}",
            "per_claim": [],
            "missed_predictions": [],
        }
    return parse_judge_response(raw)


async def run_judging(
    judge_model: str, v2_artifact: dict, min_interval: float
) -> dict[str, dict]:
    judge = build_judge_client(judge_model)
    judgements: dict[str, dict] = {}
    extractions = v2_artifact["extractions"]
    print(f"  [judge] processing {len(extractions)} posts...", flush=True)
    for idx, ext in enumerate(extractions, 1):
        post_id = ext["post_id"]
        verdict = await judge_post(
            judge,
            ext["post_text"],
            ext["post_published_at"],
            ext["claims"],
        )
        judgements[post_id] = verdict
        print(f"  [judge] {idx}/{len(extractions)} done ({post_id})", flush=True)
        if min_interval > 0:
            await asyncio.sleep(min_interval)
    return judgements


def save_judgements(
    judgements: dict[str, dict], judge_model: str, output_path: Path
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "judge": judge_model,
                },
                "judgements": {DEFAULT_MODEL_LABEL: judgements},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def compute_v2_metrics(judgements: dict[str, dict], gold_labels: list[dict]) -> dict:
    wrapped = {DEFAULT_MODEL_LABEL: judgements}
    return aggregate_metrics(wrapped, gold_labels)


def load_v1_baseline() -> dict | None:
    if not V1_REPORT_PATH.exists():
        return None
    rep = json.loads(V1_REPORT_PATH.read_text(encoding="utf-8"))
    return rep.get("per_model", {}).get("gemini/gemini-3.1-flash-lite-preview")


def apply_decision_rule(v2_metrics: dict, v1: dict | None) -> tuple[str, str]:
    if v1 is None:
        return "UNKNOWN", "V1 baseline not found — cannot compare"
    v2 = v2_metrics["per_model"][DEFAULT_MODEL_LABEL]
    ord_v1 = v1["avg_quality_score"]
    ord_v2 = v2["avg_quality_score"]
    hall_v1 = v1["hallucination_rate"]
    hall_v2 = v2["hallucination_rate"]

    ord_delta = ord_v2 - ord_v1
    hall_delta = hall_v2 - hall_v1

    if abs(ord_delta) <= 0.2 and hall_delta <= 0.05:
        verdict = "ACCEPT"
        reason = f"ordinal Δ={ord_delta:+.3f} within ±0.2; hallucination Δ={hall_delta:+.3f} ≤ +0.05"
    elif ord_delta < -0.5 or hall_delta > 0.20:
        verdict = "REJECT"
        reason = f"catastrophic regression: ordinal Δ={ord_delta:+.3f}, hallucination Δ={hall_delta:+.3f}"
    else:
        verdict = "TUNE"
        reason = f"moderate regression: ordinal Δ={ord_delta:+.3f}, hallucination Δ={hall_delta:+.3f}"
    return verdict, reason


def render_report(
    v2_metrics: dict, v1: dict | None, verdict: str, reason: str, output_path: Path
) -> None:
    v2 = v2_metrics["per_model"][DEFAULT_MODEL_LABEL]
    lines = [
        "# V2 Extraction Quality Re-evaluation Report",
        "",
        f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"**Model:** gemini/gemini-3.1-flash-lite-preview",
        f"**Prompt:** v2 (with situation field)",
        "",
        "## Decision",
        "",
        f"**Verdict:** `{verdict}`",
        f"**Reason:** {reason}",
        "",
        "## Metrics Comparison",
        "",
        "| Metric | V1 baseline | V2 (this run) | Delta |",
        "|---|---|---|---|",
    ]
    if v1:
        lines.extend([
            f"| total_claims | {v1['total_claims']} | {v2['total_claims']} | {v2['total_claims'] - v1['total_claims']:+d} |",
            f"| avg_quality_score | {v1['avg_quality_score']:.3f} | {v2['avg_quality_score']:.3f} | {v2['avg_quality_score'] - v1['avg_quality_score']:+.3f} |",
            f"| hallucination_rate | {v1['hallucination_rate']:.3f} | {v2['hallucination_rate']:.3f} | {v2['hallucination_rate'] - v1['hallucination_rate']:+.3f} |",
            f"| missed_predictions_count | {v1['missed_predictions_count']} | {v2['missed_predictions_count']} | {v2['missed_predictions_count'] - v1['missed_predictions_count']:+d} |",
        ])
    else:
        lines.append("| (V1 baseline not loaded — comparison N/A) | | | |")

    lines.extend([
        "",
        "## V2 Verdict Distribution",
        "",
    ])
    for verdict_name in VERDICT_VALUES:
        count = v2["verdict_distribution"].get(verdict_name, 0)
        lines.append(f"- `{verdict_name}`: {count}")

    lines.extend([
        "",
        "## Gold Agreement (V2)",
        "",
        f"```",
        json.dumps(v2.get("gold_agreement", {}), indent=2),
        f"```",
        "",
    ])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


async def main_async(args: argparse.Namespace) -> None:
    v2_artifact = json.loads(args.input.read_text(encoding="utf-8"))
    gold_labels = json.loads(GOLD_LABELS_PATH.read_text(encoding="utf-8"))

    print(f"Judging V2 extractions via {args.judge}")
    judgements = await run_judging(args.judge, v2_artifact, JUDGE_MIN_INTERVAL)
    save_judgements(judgements, args.judge, args.judgements_out)
    print(f"Saved judgements → {args.judgements_out}")

    metrics = compute_v2_metrics(judgements, gold_labels)
    v1 = load_v1_baseline()
    verdict, reason = apply_decision_rule(metrics, v1)
    render_report(metrics, v1, verdict, reason, args.report_out)
    print(f"Saved report → {args.report_out}")
    print(f"\nDECISION: {verdict}")
    print(f"REASON: {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 quality re-eval (Task 19.8b Stage 2)")
    parser.add_argument("--input", type=Path, default=DEFAULT_V2_EXTRACTIONS)
    parser.add_argument("--judge", default=DEFAULT_JUDGE)
    parser.add_argument("--judgements-out", type=Path, default=DEFAULT_JUDGEMENTS_OUT)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
