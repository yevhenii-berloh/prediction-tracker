#!/usr/bin/env python3
"""Detection Evaluation — Task 13.

Runs PredictionExtractor over gold-labeled posts and computes P/R/F1.
Primary use case: compare multiple LLM providers (Haiku, GPT-5-mini, Gemini,
DeepSeek, Llama) for the detection stage of the extraction pipeline.

Usage:
    # Single model
    python scripts/evaluate_detection.py --model anthropic/claude-haiku-4-5

    # All primary models sequentially
    python scripts/evaluate_detection.py --model all-primary

    # Fallback tier (run only if primary tier underperforms)
    python scripts/evaluate_detection.py --model all-fallback

Requires env vars (set only those you will use; unused providers are skipped):
    ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY,
    DEEPSEEK_API_KEY, GROQ_API_KEY
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent.parent / ".env")
except ImportError:
    pass  # dotenv optional; fallback to manual env vars

import litellm

# Drop provider-unsupported params (e.g., GPT-5 family rejects temperature != 1.0).
# Without this, incompatible params raise UnsupportedParamsError which the extractor
# silently catches — making all posts appear as "no predictions" (invisible failure).
# Trade-off: GPT-5 eval runs at temperature=1.0 (stochastic); this is noted in reports.
litellm.drop_params = True

from prophet_checker.analysis.extractor import PredictionExtractor
from prophet_checker.llm.client import LLMClient

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent
GOLD_LABELS_PATH = PROJECT_ROOT / "scripts" / "data" / "extraction" / "gold_labels.json"
SAMPLE_POSTS_PATH = PROJECT_ROOT / "scripts" / "data" / "raw" / "samples" / "sample_posts.json"
RESULTS_DIR = PROJECT_ROOT / "scripts" / "outputs" / "detection_eval"

PROVIDER_API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
}

PRIMARY_MODELS = [
    "anthropic/claude-haiku-4-5",
    "openai/gpt-5-mini",
    "gemini/gemini-3.1-flash-lite-preview",  # 3.1 Flash Lite still preview as of 2026-04
    "deepseek/deepseek-chat",
    "groq/llama-3.3-70b-versatile",
]

FALLBACK_MODELS = [
    "anthropic/claude-sonnet-4-6",
]

# Provider-specific concurrency limits (rate-limit aware).
# Default concurrency is used when model_id not in this map.
CONCURRENCY_OVERRIDES = {
    # Groq free tier has TPM limit (~12k tokens/minute), not RPM — concurrency=1
    # combined with MIN_CALL_INTERVAL_SECONDS delay keeps us under.
    "groq/llama-3.3-70b-versatile": 1,
    # Gemini preview models on free tier have very low RPM. Combined with
    # MIN_CALL_INTERVAL_SECONDS sleep, we throttle to ~8 RPM safely.
    "gemini/gemini-3.1-flash-lite-preview": 1,
}

# Forced minimum interval between consecutive calls to a model (only effective
# when paired with concurrency=1). Use to stay under strict free-tier RPM limits
# when LiteLLM's built-in retries can't keep up.
MIN_CALL_INTERVAL_SECONDS = {
    "gemini/gemini-3.1-flash-lite-preview": 1.0,  # ~8.5 RPM, under free tier limit
    "groq/llama-3.3-70b-versatile": 13.0,  # ~4.6 RPM, under TPM limit for ~2.5k tok/call
}

CONCURRENCY_OVERRIDES.setdefault("anthropic/claude-opus-4-6", 1)
MIN_CALL_INTERVAL_SECONDS.setdefault("anthropic/claude-opus-4-6", 8.0)
CONCURRENCY_OVERRIDES.setdefault("gemini/gemini-2.5-pro", 2)
MIN_CALL_INTERVAL_SECONDS.setdefault("gemini/gemini-2.5-pro", 1.0)
CONCURRENCY_OVERRIDES.setdefault("openai/gpt-5", 5)
MIN_CALL_INTERVAL_SECONDS.setdefault("openai/gpt-5", 0.0)


# =============================================================================
# Metrics
# =============================================================================


def compute_metrics(gold: list[bool], preds: list[bool]) -> dict:
    """Compute precision / recall / F1 + confusion matrix from aligned lists.

    Returns dict with keys: precision, recall, f1, confusion, total.
    - precision = TP / (TP + FP), guarded to 0.0 if denominator is 0
    - recall    = TP / (TP + FN), None if denominator is 0 (undefined: no positives)
    - f1        = 2*P*R / (P+R), 0.0 if recall is None or precision+recall == 0

    Raises:
        ValueError: if ``gold`` and ``preds`` have different lengths.
    """
    if len(gold) != len(preds):
        raise ValueError(
            f"gold and preds must have equal lengths, got {len(gold)} vs {len(preds)}"
        )

    TP = sum(1 for g, p in zip(gold, preds) if g and p)
    FP = sum(1 for g, p in zip(gold, preds) if not g and p)
    FN = sum(1 for g, p in zip(gold, preds) if g and not p)
    TN = sum(1 for g, p in zip(gold, preds) if not g and not p)

    precision = TP / (TP + FP) if (TP + FP) > 0 else 0.0

    recall_denom = TP + FN
    if recall_denom > 0:
        recall = TP / recall_denom
    else:
        recall = None  # undefined: no actual positives in gold

    if recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0

    return {
        "confusion": {"TP": TP, "FP": FP, "FN": FN, "TN": TN},
        "precision": round(precision, 3),
        "recall": round(recall, 3) if recall is not None else None,
        "f1": round(f1, 3),
        "total": len(gold),
    }


# =============================================================================
# Classification bridge
# =============================================================================


async def classify_post(extractor, post: dict) -> bool | None:
    """Classify a single post as containing a prediction (True/False) or error (None).

    Maps post dict fields to PredictionExtractor.extract() arguments:
        post["text"]         -> text
        post["person_name"]  -> person_id  (no PersonRepo yet; name is unique enough)
        post["id"]           -> document_id
        post["person_name"]  -> person_name
        post["published_at"] -> published_date (ISO date string)

    Returns:
        True   if extractor returned ≥1 prediction
        False  if extractor returned an empty list
        None   if extractor raised (API down, malformed response, etc.)
    """
    try:
        outcome = await extractor.extract(
            text=post["text"],
            person_id=post["person_name"],
            document_id=post["id"],
            person_name=post["person_name"],
            published_date=post["published_at"],
        )
        return len(outcome.predictions) > 0
    except Exception:
        logger.exception("Extractor failed on post %s", post.get("id"))
        return None


# =============================================================================
# Extractor factory
# =============================================================================


def _parse_model_id(model_id: str) -> tuple[str, str]:
    """Split 'provider/model' into (provider, model). Raises if no slash."""
    if "/" not in model_id:
        raise ValueError(
            f"model_id must be 'provider/model', got {model_id!r}. "
            f"Example: {PRIMARY_MODELS[0]}"
        )
    provider, model = model_id.split("/", 1)
    return provider, model


def _default_extractor_factory(
    model_id: str, system_prompt: str | None = None
) -> PredictionExtractor:
    """Build a PredictionExtractor for the given model_id using env-var API keys."""
    provider, model = _parse_model_id(model_id)

    if provider not in PROVIDER_API_KEY_ENV:
        raise ValueError(
            f"Unknown provider {provider!r}. "
            f"Supported: {list(PROVIDER_API_KEY_ENV.keys())}"
        )

    env_var = PROVIDER_API_KEY_ENV[provider]
    api_key = os.environ.get(env_var)
    if not api_key:
        raise RuntimeError(
            f"Missing API key for provider {provider!r}: set env var {env_var}"
        )

    client = LLMClient(
        provider=provider,
        model=model,
        api_key=api_key,
        temperature=0.0,
    )
    return PredictionExtractor(client, system_prompt=system_prompt)


# =============================================================================
# Orchestration
# =============================================================================


async def run_evaluation_for_model(
    model_id: str,
    gold_labels: list[dict],
    posts: list[dict],
    author_filter: str = "Арестович",
    concurrency: int = 5,
    extractor_factory=None,
    min_call_interval_seconds: float = 0.0,
) -> dict:
    """Evaluate one model on gold-labeled posts of the specified author.

    Args:
        model_id: 'provider/model' string (e.g. 'anthropic/claude-haiku-4-5').
        gold_labels: list of ``{"id": str, "has_prediction": bool}`` dicts.
        posts: list of post dicts with keys ``id, person_name, published_at, text``.
        author_filter: only posts with this person_name are evaluated.
        concurrency: number of parallel classifications.
        extractor_factory: callable taking model_id, returning an extractor-like
            object. Defaults to building real LLMClient. Override for testing.

    Returns:
        Report dict with keys: model, author_filter, n_evaluated, n_errors,
        precision, recall, f1, confusion, total, false_positives,
        false_negatives, errors.
    """
    if extractor_factory is None:
        extractor_factory = _default_extractor_factory

    extractor = extractor_factory(model_id)

    # Index posts by id for O(1) lookup
    posts_by_id = {p["id"]: p for p in posts}

    # Join gold + posts, filter by author
    rows: list[dict] = []
    for g in gold_labels:
        post = posts_by_id.get(g["id"])
        if post is None:
            continue  # orphan gold label (post not in corpus)
        if post["person_name"] != author_filter:
            continue
        rows.append({"gold": g["has_prediction"], "post": post})

    # Concurrent classification with bounded parallelism
    sem = asyncio.Semaphore(concurrency)

    async def process(row: dict) -> dict:
        async with sem:
            row["predicted"] = await classify_post(extractor, row["post"])
            # Forced throttle for strict-rate-limit providers. Only meaningful when
            # concurrency=1 (else parallel tasks may bypass the sleep window).
            if min_call_interval_seconds > 0:
                await asyncio.sleep(min_call_interval_seconds)
        return row

    results = await asyncio.gather(*(process(r) for r in rows))

    # Separate successful classifications from errors
    valid = [r for r in results if r["predicted"] is not None]
    errors = [r for r in results if r["predicted"] is None]

    # Compute metrics on valid classifications only
    gold_list = [r["gold"] for r in valid]
    pred_list = [r["predicted"] for r in valid]
    metrics = compute_metrics(gold_list, pred_list)

    # Error-analysis payload: FP and FN with text previews (200 chars)
    false_positives = [
        {"id": r["post"]["id"], "text_preview": r["post"]["text"][:200]}
        for r in valid
        if not r["gold"] and r["predicted"]
    ]
    false_negatives = [
        {"id": r["post"]["id"], "text_preview": r["post"]["text"][:200]}
        for r in valid
        if r["gold"] and not r["predicted"]
    ]

    return {
        "model": model_id,
        "author_filter": author_filter,
        "n_evaluated": len(valid),
        "n_errors": len(errors),
        **metrics,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "errors": [{"id": r["post"]["id"]} for r in errors],
    }


# =============================================================================
# CLI entry point
# =============================================================================


def _resolve_models(arg: str) -> list[str]:
    if arg == "all-primary":
        return list(PRIMARY_MODELS)
    if arg == "all-fallback":
        return list(FALLBACK_MODELS)
    return [arg]


def _print_model_summary(report: dict, output_path: Path) -> None:
    c = report["confusion"]
    r = report["recall"]
    r_str = f"{r:.3f}" if r is not None else "N/A"
    print(f"  P={report['precision']:.3f}  R={r_str}  F1={report['f1']:.3f}")
    print(f"  Confusion: TP={c['TP']} FP={c['FP']} FN={c['FN']} TN={c['TN']}")
    print(f"  Evaluated: {report['n_evaluated']}  Errors: {report['n_errors']}")
    print(f"  Saved: {output_path}")


def _print_comparison_table(summary: list[dict]) -> None:
    if len(summary) < 2:
        return
    print(f"\n{'=' * 86}")
    print("COMPARISON")
    print(f"{'=' * 86}")
    print(f"{'Model':<45} {'P':>7} {'R':>7} {'F1':>7} {'Errors':>7} {'Eval':>7}")
    print(f"{'-' * 86}")
    for r in summary:
        recall_str = f"{r['recall']:.3f}" if r["recall"] is not None else "N/A"
        print(
            f"{r['model']:<45} "
            f"{r['precision']:>7.3f} {recall_str:>7} {r['f1']:>7.3f} "
            f"{r['n_errors']:>7} {r['n_evaluated']:>7}"
        )
    print(f"{'=' * 86}\n")


async def _main_async(args: argparse.Namespace) -> None:
    # Load corpus + gold
    gold_labels = json.loads(GOLD_LABELS_PATH.read_text(encoding="utf-8"))
    posts = json.loads(SAMPLE_POSTS_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(gold_labels)} gold labels and {len(posts)} posts.")

    models = _resolve_models(args.model)
    print(f"Will evaluate {len(models)} model(s): {models}")

    summary: list[dict] = []
    for model_id in models:
        print(f"\n{'=' * 60}")
        print(f"  {model_id}")
        print(f"{'=' * 60}")

        effective_concurrency = CONCURRENCY_OVERRIDES.get(model_id, args.concurrency)
        min_interval = MIN_CALL_INTERVAL_SECONDS.get(model_id, 0.0)
        if effective_concurrency != args.concurrency:
            print(f"  (concurrency override: {effective_concurrency})")
        if min_interval > 0:
            est_min = (len(gold_labels) * min_interval) / 60
            print(f"  (min interval: {min_interval}s/call → ≥{est_min:.1f} min wall)")

        try:
            report = await run_evaluation_for_model(
                model_id=model_id,
                gold_labels=gold_labels,
                posts=posts,
                author_filter=args.author,
                concurrency=effective_concurrency,
                min_call_interval_seconds=min_interval,
            )
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            logger.exception("run_evaluation_for_model failed for %s", model_id)
            continue

        safe_name = model_id.replace("/", "_")
        out_path = RESULTS_DIR / f"detection_results_{safe_name}.json"
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        _print_model_summary(report, out_path)
        summary.append(report)

    _print_comparison_table(summary)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Task 13: Detection Evaluation on gold-labeled posts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"Primary models: {', '.join(PRIMARY_MODELS)}\n"
            f"Fallback models: {', '.join(FALLBACK_MODELS)}"
        ),
    )
    parser.add_argument(
        "--model",
        default="all-primary",
        help="Model ID (e.g. 'anthropic/claude-haiku-4-5') or "
        "'all-primary' / 'all-fallback'.",
    )
    parser.add_argument(
        "--author",
        default="Арестович",
        help="Only evaluate posts where person_name matches (default: Арестович).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max parallel API calls (overridden per-model if in CONCURRENCY_OVERRIDES).",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
