#!/usr/bin/env python3
"""Extraction Quality Evaluation — Task 13.5 (LLM-as-judge).

Документація + приклади запуску: scripts/extraction/extraction_quality_eval.md
Spec: docs/extraction-quality-eval/2026-04-21-extraction-quality-eval-design.md
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Add src/ and scripts/ to path for prophet_checker + sibling package imports.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

# Load .env early so provider API keys (ANTHROPIC_API_KEY, GEMINI_API_KEY, ...)
# are available to litellm regardless of whether the user exported them in shell.
# override=True is critical: parent shells sometimes export keys as empty strings,
# which load_dotenv treats as "already set" and skips by default — losing the real
# values from .env.
try:
    from dotenv import load_dotenv  # noqa: E402

    load_dotenv(Path(__file__).parent.parent.parent / ".env", override=True)
except ImportError:
    pass  # dotenv optional; fallback to environment

from extraction.judge_prompts import (  # noqa: E402
    JUDGE_SYSTEM,
    VERDICT_ORDINAL,
    VERDICT_VALUES,
    build_judge_prompt,
    parse_judge_response,
)

logger = logging.getLogger(__name__)

# Import Task 13 evaluation harness components — reused without modification
from extraction.detection_eval import (  # noqa: E402
    CONCURRENCY_OVERRIDES,
    MIN_CALL_INTERVAL_SECONDS,
    PROVIDER_API_KEY_ENV,
    _default_extractor_factory,
)
from prophet_checker.llm.client import LLMClient  # noqa: E402


# =============================================================================
# Aggregation (pure)
# =============================================================================


def _empty_distribution() -> dict[str, int]:
    return {v: 0 for v in VERDICT_VALUES}


def aggregate_metrics(
    judgements: dict, gold_labels: list[dict] | None = None
) -> dict:
    """Compute per-model summary report from judgements + gold labels.

    Args:
        judgements: {extractor_id: {post_id: {per_claim: [...], missed_predictions: [...], parse_error: str|None}}}
        gold_labels: list of {"id": str, "has_prediction": bool}

    Returns:
        {"per_model": {extractor_id: {...metrics...}}}

    Posts with `parse_error` set (judge JSON malformed) are counted in
    `parse_error_count` but EXCLUDED from gold_agreement matrix — the
    failure is on the judge infrastructure, not the extractor model, so
    counting them as "no valid extractions" would unfairly penalize the
    extractor.
    """
    no_gold = not gold_labels
    gold_index = {} if no_gold else {g["id"]: g["has_prediction"] for g in gold_labels}
    per_model: dict[str, dict] = {}

    for extractor_id, posts in judgements.items():
        verdict_counts = _empty_distribution()
        invalid_count = 0
        parse_error_count = 0
        ordinal_sum = 0
        ordinal_n = 0
        missed_total = 0
        gold_yes_with_valid = 0
        gold_yes_no_valid = 0
        gold_no_with_valid = 0
        gold_no_no_valid = 0

        for post_id, j in posts.items():
            # Skip parse-error posts entirely (infra failure, not model failure).
            # Counted for visibility but excluded from gold_agreement matrix.
            if j.get("parse_error") is not None:
                parse_error_count += 1
                continue

            claims = j.get("per_claim", [])
            missed = j.get("missed_predictions", [])
            missed_total += len(missed)

            has_valid_extraction = False
            for c in claims:
                v = c.get("verdict")
                if c.get("verdict_invalid") or v not in VERDICT_VALUES:
                    invalid_count += 1
                    continue
                verdict_counts[v] += 1
                ordinal_sum += VERDICT_ORDINAL[v]
                ordinal_n += 1
                if VERDICT_ORDINAL[v] >= 2:  # exact_match, faithful_paraphrase, valid_but_metadata_error
                    has_valid_extraction = True

            gold_yes = gold_index.get(post_id)
            if gold_yes is True:
                if has_valid_extraction:
                    gold_yes_with_valid += 1
                else:
                    gold_yes_no_valid += 1
            elif gold_yes is False:
                if has_valid_extraction:
                    gold_no_with_valid += 1
                else:
                    gold_no_no_valid += 1

        total_claims = sum(verdict_counts.values()) + invalid_count
        avg_score = (ordinal_sum / ordinal_n) if ordinal_n > 0 else 0.0
        hallucination_rate = (
            verdict_counts["hallucination"] / total_claims
            if total_claims > 0
            else 0.0
        )
        gold_yes_total = gold_yes_with_valid + gold_yes_no_valid
        missed_rate = (missed_total / gold_yes_total) if gold_yes_total > 0 else 0.0

        per_model[extractor_id] = {
            "total_claims": total_claims,
            "invalid_verdict_count": invalid_count,
            "parse_error_count": parse_error_count,
            "verdict_distribution": verdict_counts,
            # Float values stored at full precision; rounding happens only
            # in the CLI display layer to avoid lossy aggregation.
            "avg_quality_score": avg_score,
            "hallucination_rate": hallucination_rate,
            "missed_predictions_count": missed_total,
            "missed_rate": None if no_gold else missed_rate,
            "gold_agreement": None if no_gold else {
                "gold_YES_with_valid_extraction": gold_yes_with_valid,
                "gold_YES_no_valid_extraction": gold_yes_no_valid,
                "gold_NO_with_extractions_labeled_valid": gold_no_with_valid,
                "gold_NO_without_valid_extractions": gold_no_no_valid,
            },
        }

    return {"per_model": per_model}


# =============================================================================
# Stage 1 — extraction orchestration
# =============================================================================


def _serialize_prediction(p) -> dict:
    """Convert a Prediction domain object to JSON-friendly dict."""
    return {
        "claim_text": p.claim_text,
        "prediction_date": p.prediction_date.isoformat() if p.prediction_date else None,
        "target_date": p.target_date.isoformat() if p.target_date else None,
        "topic": p.topic,
    }


async def run_stage1_extraction(
    extractors: list[str],
    posts: list[dict],
    author_filter: str,
    output_path: Path,
    extractor_factory: Callable,
    concurrency: int = 5,
    per_model_concurrency: dict[str, int] | None = None,
    per_model_min_interval: dict[str, float] | None = None,
) -> None:
    """Run each extractor over filtered posts, save full extractions to disk.

    Errors during extraction are logged into a separate `errors` map per model;
    the post still appears in `extractions` with an empty claims list.

    Per-model rate-limit safety:
      - per_model_concurrency: override semaphore size for specific models
      - per_model_min_interval: forced sleep (seconds) after each call,
        only effective when concurrency=1 (otherwise parallel tasks bypass it)
    """
    filtered_posts = [p for p in posts if p["person_name"] == author_filter]
    extractions: dict[str, dict[str, list[dict]]] = {m: {} for m in extractors}
    errors: dict[str, dict[str, str]] = {m: {} for m in extractors}
    per_model_concurrency = per_model_concurrency or {}
    per_model_min_interval = per_model_min_interval or {}

    for model_id in extractors:
        extractor = extractor_factory(model_id)
        effective_concurrency = per_model_concurrency.get(model_id, concurrency)
        min_interval = per_model_min_interval.get(model_id, 0.0)
        sem = asyncio.Semaphore(effective_concurrency)
        print(
            f"  [stage1 {model_id}] starting: {len(filtered_posts)} posts, "
            f"concurrency={effective_concurrency}, interval={min_interval}s",
            flush=True,
        )

        async def process(post: dict) -> tuple[str, list[dict], str | None]:
            async with sem:
                try:
                    preds = await extractor.extract(
                        text=post["text"],
                        person_id=post["person_name"],
                        document_id=post["id"],
                        person_name=post["person_name"],
                        published_date=post["published_at"],
                    )
                    result = post["id"], [_serialize_prediction(p) for p in preds], None
                except Exception as e:
                    logger.exception(
                        "Extraction failed for %s on %s", model_id, post["id"]
                    )
                    result = post["id"], [], f"{type(e).__name__}: {e}"
                if min_interval > 0:
                    await asyncio.sleep(min_interval)
                return result

        total = len(filtered_posts)
        completed = 0
        for coro in asyncio.as_completed([process(p) for p in filtered_posts]):
            post_id, claims, err = await coro
            completed += 1
            if err:
                print(
                    f"  ✗ [stage1 {model_id} {completed}/{total}] {post_id}: {err}",
                    flush=True,
                )
            else:
                print(
                    f"  [stage1 {model_id} {completed}/{total}] "
                    f"{post_id}: {len(claims)} claims",
                    flush=True,
                )
            extractions[model_id][post_id] = claims
            if err:
                errors[model_id][post_id] = err
        n_claims = sum(len(c) for c in extractions[model_id].values())
        print(
            f"  [stage1 {model_id}] done: {total} posts, {n_claims} claims, "
            f"{len(errors[model_id])} errors",
            flush=True,
        )

    # Merge with existing artifact if present — preserves data for extractors
    # not in the current run, replaces data for extractors that are.
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        merged_ext = dict(existing.get("extractions", {}))
        merged_err = dict(existing.get("errors", {}))
        for m in extractors:
            merged_ext[m] = extractions[m]
            merged_err[m] = errors[m]
        extractions = merged_ext
        errors = merged_err

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "dataset_size": len(filtered_posts),
                    "extractors": sorted(extractions.keys()),
                    "author_filter": author_filter,
                },
                "extractions": extractions,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# =============================================================================
# Stage 2 — judge orchestration
# =============================================================================


async def run_stage2_judge(
    judge_model: str,
    extractions_path: Path,
    posts: list[dict],
    output_path: Path,
    judge_factory: Callable,
    concurrency: int = 1,
    min_call_interval_seconds: float = 0.0,
    extractors_filter: set[str] | None = None,
) -> None:
    """For each (extractor, post, claims) call the judge and save verdicts.

    Posts that errored in Stage 1 are skipped (marked in judgements artifact).
    Judge response parse failures preserve `parse_error` field.

    `extractors_filter` (optional): if set, only judge claims from these
    extractor models. Useful for incremental runs after adding a new model
    — old extractors' judgements stay intact via merge-mode below.

    `posts` already constrains which post_ids are judged; pass a filtered
    list (e.g. gold-only) to avoid spending judge time on irrelevant posts.

    After completion, prints per-extractor parse-error counts to stderr+console
    so the user can see infra issues at a glance (per Task 13.5 plan revision).
    """
    extractions_artifact = json.loads(extractions_path.read_text(encoding="utf-8"))
    extractions = extractions_artifact["extractions"]
    if extractors_filter is not None:
        extractions = {m: v for m, v in extractions.items() if m in extractors_filter}
    errors_map = extractions_artifact.get("errors", {})
    posts_by_id = {p["id"]: p for p in posts}
    allowed_post_ids = set(posts_by_id.keys())

    judge_client = judge_factory(judge_model)
    judgements: dict[str, dict[str, dict]] = {m: {} for m in extractions}

    sem = asyncio.Semaphore(concurrency)

    async def judge_one(
        model_id: str, post_id: str, claims: list[dict]
    ) -> tuple[str, str, dict]:
        # Skip posts that errored in Stage 1
        if post_id in errors_map.get(model_id, {}):
            return model_id, post_id, {
                "skipped_due_to_extraction_error": True,
                "per_claim": [],
                "missed_predictions": [],
            }

        post = posts_by_id.get(post_id)
        if post is None:
            return model_id, post_id, {
                "skipped_post_not_found": True,
                "per_claim": [],
                "missed_predictions": [],
            }

        prompt = build_judge_prompt(
            post_text=post["text"],
            published_date=post["published_at"],
            extracted_claims=claims,
        )
        async with sem:
            try:
                raw = await judge_client.complete(prompt, system=JUDGE_SYSTEM)
            except Exception as e:
                logger.exception(
                    "Judge call failed: %s / %s", model_id, post_id
                )
                print(
                    f"  ✗ [stage2 {model_id}] {post_id}: {type(e).__name__}: {e}",
                    flush=True,
                )
                return model_id, post_id, {
                    "judge_error": f"{type(e).__name__}: {e}",
                    "per_claim": [],
                    "missed_predictions": [],
                }
            if min_call_interval_seconds > 0:
                await asyncio.sleep(min_call_interval_seconds)

        parsed = parse_judge_response(raw)
        if parsed.get("parse_error"):
            print(
                f"  ✗ [stage2 {model_id}] {post_id}: "
                f"judge parse error: {parsed['parse_error']}",
                flush=True,
            )
        return model_id, post_id, parsed

    tasks = [
        judge_one(m, pid, claims)
        for m, posts_dict in extractions.items()
        for pid, claims in posts_dict.items()
        if pid in allowed_post_ids
    ]
    print(f"  [stage2] judging {len(tasks)} (extractor, post) pairs...", flush=True)
    # Stream progress: log each completion via as_completed instead of gather
    results = []
    completed = 0
    for coro in asyncio.as_completed(tasks):
        model_id, post_id, parsed = await coro
        results.append((model_id, post_id, parsed))
        completed += 1
        if completed % 5 == 0 or completed == len(tasks):
            print(
                f"  [stage2 {completed}/{len(tasks)}] last: {model_id} / {post_id}",
                flush=True,
            )
    for model_id, post_id, parsed in results:
        judgements[model_id][post_id] = parsed

    # Surface parse_error count to console for visibility (infra signal,
    # not model-quality signal). Aggregator excludes these from gold_agreement.
    parse_error_summary: dict[str, int] = {}
    for model_id, posts_dict in judgements.items():
        n_errors = sum(1 for p in posts_dict.values() if p.get("parse_error"))
        if n_errors > 0:
            parse_error_summary[model_id] = n_errors
    if parse_error_summary:
        logger.warning(
            "Judge parse failures: %s. Excluded from gold_agreement matrix.",
            parse_error_summary,
        )
        print(f"  [stage2] judge parse failures: {parse_error_summary}")

    # Merge with existing artifact if present — preserves judgements for
    # extractors not in the current run, replaces those that are.
    if output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        merged = dict(existing.get("judgements", {}))
        for m in judgements:
            merged[m] = judgements[m]
        judgements = merged

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "judge": judge_model,
                    "source_extractions": str(extractions_path),
                },
                "judgements": judgements,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# =============================================================================
# Stage 3 — aggregate report
# =============================================================================


def run_stage3_aggregate(
    judgements_path: Path,
    gold_labels_path: Path | None,
    output_path: Path,
) -> dict:
    """Load judgements + gold, compute per-model report, save to disk.

    Returns the report dict for in-process use (CLI prints summary table).
    """
    judgements_artifact = json.loads(judgements_path.read_text(encoding="utf-8"))
    judgements = judgements_artifact["judgements"]
    gold_labels = None if gold_labels_path is None else json.loads(gold_labels_path.read_text(encoding="utf-8"))

    report = aggregate_metrics(judgements=judgements, gold_labels=gold_labels)
    report["metadata"] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_judgements": str(judgements_path),
        "source_gold": str(gold_labels_path) if gold_labels_path else None,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


# =============================================================================
# CLI orchestration
# =============================================================================


PRIMARY_EXTRACTORS: tuple[str, ...] = (
    "gemini/gemini-3.1-flash-lite-preview",
    "deepseek/deepseek-chat",
    "anthropic/claude-sonnet-4-6",
    "gemini/gemini-3-flash-preview",  # added 2026-04-21 for cross-tier Gemini comparison
)
DEFAULT_JUDGE = "anthropic/claude-opus-4-6"

# Extend Task 13 throttle dicts with Task 13.5-specific judge limits.
# Anthropic Opus 4.6 has 30,000 ITPM on lower tiers. Each judge call is
# ~1500 input tokens (post + claims + JUDGE_SYSTEM guidelines). To stay
# under 30k ITPM with margin: concurrency=1 + 4s sleep = 15 RPM × 1500 = 22.5k ITPM.
CONCURRENCY_OVERRIDES.setdefault("anthropic/claude-opus-4-6", 1)
# Anthropic 30k ITPM tier: ~3k tokens/call × 7.5 RPM = ~22.5k ITPM, safely under
# limit. Earlier 4s gave 15 RPM = 45k ITPM and triggered silent rate-limit deaths.
MIN_CALL_INTERVAL_SECONDS.setdefault("anthropic/claude-opus-4-6", 8.0)
# Gemini 3 Flash Preview shares free-tier 15 RPM with Flash Lite Preview.
CONCURRENCY_OVERRIDES.setdefault("gemini/gemini-3-flash-preview", 1)
MIN_CALL_INTERVAL_SECONDS.setdefault("gemini/gemini-3-flash-preview", 7.0)
# Gemini 3.1 Pro Preview — Tier 1 paid tier gives ~150 RPM; throttle to ~30 RPM
# (concurrency=2 + 1s) for safety against bursty token-per-minute caps.
CONCURRENCY_OVERRIDES.setdefault("gemini/gemini-3.1-pro-preview", 2)
MIN_CALL_INTERVAL_SECONDS.setdefault("gemini/gemini-3.1-pro-preview", 1.0)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_GOLD_PATH = PROJECT_ROOT / "scripts" / "data" / "gold_labels.json"
DEFAULT_POSTS_PATH = PROJECT_ROOT / "scripts" / "data" / "sample_posts.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "scripts" / "outputs" / "extraction_eval"


def _judge_factory(model_id: str) -> LLMClient:
    """Build LLMClient for the judge model.

    Reuses provider/key wiring conventions from Task 13's _default_extractor_factory
    but returns a bare LLMClient (no PredictionExtractor wrapper — judge calls
    .complete directly without embedding any predictions).
    """
    if "/" not in model_id:
        raise ValueError(
            f"judge model_id must be 'provider/model', got {model_id!r}"
        )
    provider, model = model_id.split("/", 1)
    if provider not in PROVIDER_API_KEY_ENV:
        raise ValueError(f"Unknown provider for judge: {provider!r}")
    api_key = os.environ.get(PROVIDER_API_KEY_ENV[provider])
    if not api_key:
        raise RuntimeError(
            f"Missing API key for judge provider {provider!r}: "
            f"set env var {PROVIDER_API_KEY_ENV[provider]}"
        )
    return LLMClient(
        provider=provider, model=model, api_key=api_key, temperature=0.0
    )


def _parse_stages(s: str) -> set[int]:
    return {int(x.strip()) for x in s.split(",") if x.strip()}


def _format_eta(n_calls: int, concurrency: int, min_interval: float) -> str:
    """Throttle-bound ETA: both stages sleep min_interval inside the semaphore,
    so effective throughput is concurrency / min_interval. Without a throttle
    the wall time is dominated by unknown per-call latency — no fake estimate.
    """
    if min_interval <= 0:
        return f"no throttle (concurrency={concurrency})"
    seconds = n_calls * min_interval / max(concurrency, 1)
    if seconds < 60:
        return f"~{seconds:.0f}s"
    return f"~{seconds / 60:.1f} min"


def _load_filtered_posts(args: argparse.Namespace) -> tuple[list[dict], dict[str, int]]:
    """Load posts and apply --gold-only / --limit, recording counts per step.

    The --author filter stays inside run_stage1_extraction — here it is only
    *counted* (counts["after_author"]), so the run plan shows the real call
    volume without changing what the stages receive.
    """
    posts = json.loads(Path(args.posts).read_text(encoding="utf-8"))
    counts: dict[str, int] = {"pool": len(posts)}
    if args.gold_only:
        gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
        gold_ids = {g["id"] for g in gold}
        posts = [p for p in posts if p["id"] in gold_ids]
        counts["after_gold_only"] = len(posts)
    if args.limit is not None:
        posts = [p for p in posts if p["person_name"] == args.author][: args.limit]
        counts["after_limit"] = len(posts)
    counts["after_author"] = sum(1 for p in posts if p["person_name"] == args.author)
    return posts, counts


def _format_run_plan(
    counts: dict[str, int],
    extractors: list[str],
    judge_model: str,
    stages: set[int],
    author: str,
    overrides: dict[str, int],
    intervals: dict[str, float],
) -> str:
    """Pre-flight summary printed before the first API call: how many posts
    survived each filter, how many calls each stage will make, and ETA per
    model derived from the throttle tables.
    """
    n_posts = counts["after_author"]
    chain = [f"{counts['pool']} pool"]
    if "after_gold_only" in counts:
        chain.append(f"{counts['after_gold_only']} gold-only")
    if "after_limit" in counts:
        chain.append(f"{counts['after_limit']} limit")
    chain.append(f"author {author!r}: {n_posts}")
    lines = ["Run plan:", f"  posts: {' → '.join(chain)}"]

    n_pairs = n_posts * len(extractors)
    if 1 in stages:
        lines.append(
            f"  stage 1: {n_posts} posts × {len(extractors)} extractors = {n_pairs} calls"
        )
        for m in extractors:
            conc = overrides.get(m, 5)
            interval = intervals.get(m, 0.0)
            lines.append(
                f"    {m}  concurrency={conc}  interval={interval}s  "
                f"ETA {_format_eta(n_posts, conc, interval)}"
            )
    if 2 in stages:
        conc = overrides.get(judge_model, 3)
        interval = intervals.get(judge_model, 0.0)
        # Stage 2 alone reads pairs from extraction_outputs.json — the exact
        # count is printed by run_stage2_judge; here it's an upper bound.
        pairs_label = str(n_pairs) if 1 in stages else f"up to {n_pairs}"
        lines.append(
            f"  stage 2: {pairs_label} judge pairs, judge={judge_model}  "
            f"concurrency={conc}  interval={interval}s  "
            f"ETA {_format_eta(n_pairs, conc, interval)}"
        )
    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Task 13.5 — Extraction Quality Evaluation (LLM-as-judge)"
    )
    parser.add_argument(
        "--stages",
        default="1,2,3",
        help="Comma-separated stage numbers to run (default: 1,2,3)",
    )
    parser.add_argument(
        "--extractors",
        default=",".join(PRIMARY_EXTRACTORS),
        help="Comma-separated extractor model IDs",
    )
    parser.add_argument(
        "--judge", default=DEFAULT_JUDGE, help="Judge model ID"
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Where to write JSON artifacts",
    )
    parser.add_argument(
        "--gold",
        default=str(DEFAULT_GOLD_PATH),
        help="Path to gold_labels.json",
    )
    parser.add_argument(
        "--posts",
        default=str(DEFAULT_POSTS_PATH),
        help="Path to sample_posts.json",
    )
    parser.add_argument(
        "--author",
        default="Арестович",
        help="Filter posts by person_name",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit posts processed (for dry run / debugging)",
    )
    parser.add_argument(
        "--gold-only",
        action="store_true",
        default=False,
        help="Process only posts that appear in gold_labels.json (97 for Arestovich)",
    )
    parser.add_argument(
        "--no-gold",
        action="store_true",
        default=False,
        help="Run without gold labels (gold-derived fields -> null)",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> None:
    stages = _parse_stages(args.stages)
    out_dir = Path(args.output_dir)
    extractors = [e.strip() for e in args.extractors.split(",") if e.strip()]
    extractions_path = out_dir / "extraction_outputs.json"
    judgements_path = out_dir / "extraction_judgements.json"
    report_path = out_dir / "extraction_eval_report.json"

    posts: list[dict] = []
    if stages & {1, 2}:
        posts, counts = _load_filtered_posts(args)
        print(
            _format_run_plan(
                counts=counts,
                extractors=extractors,
                judge_model=args.judge,
                stages=stages,
                author=args.author,
                overrides=CONCURRENCY_OVERRIDES,
                intervals=MIN_CALL_INTERVAL_SECONDS,
            ),
            flush=True,
        )

    if 1 in stages:
        print(
            f"Stage 1: extracting with {len(extractors)} models "
            f"on {args.author} posts"
        )
        await run_stage1_extraction(
            extractors=extractors,
            posts=posts,
            author_filter=args.author,
            output_path=extractions_path,
            extractor_factory=_default_extractor_factory,
            per_model_concurrency=CONCURRENCY_OVERRIDES,
            per_model_min_interval=MIN_CALL_INTERVAL_SECONDS,
        )
        print(f"  ✓ saved {extractions_path}")

    if 2 in stages:
        # Per-judge concurrency override (Opus paid tier supports higher concurrency)
        concurrency = CONCURRENCY_OVERRIDES.get(args.judge, 3)
        min_interval = MIN_CALL_INTERVAL_SECONDS.get(args.judge, 0.0)
        # If --extractors was passed, judge only those models' claims and merge
        # with existing judgements for everyone else (incremental mode).
        extractors_filter = set(extractors) if extractors else None
        print(
            f"Stage 2: judging with {args.judge} (concurrency={concurrency})"
            + (f", filter={sorted(extractors_filter)}" if extractors_filter else "")
        )
        await run_stage2_judge(
            judge_model=args.judge,
            extractions_path=extractions_path,
            posts=posts,
            output_path=judgements_path,
            judge_factory=_judge_factory,
            concurrency=concurrency,
            min_call_interval_seconds=min_interval,
            extractors_filter=extractors_filter,
        )
        print(f"  ✓ saved {judgements_path}")

    if 3 in stages:
        print("Stage 3: aggregating metrics")
        report = run_stage3_aggregate(
            judgements_path=judgements_path,
            gold_labels_path=None if args.no_gold else Path(args.gold),
            output_path=report_path,
        )
        _print_report_table(report)
        print(f"  ✓ saved {report_path}")


def _print_report_table(report: dict) -> None:
    print("\n" + "=" * 92)
    print(
        f"{'Model':<48} {'avg_score':>10} {'hall_rate':>10} {'missed':>8} {'claims':>7}"
    )
    print("-" * 92)
    for m, mr in report["per_model"].items():
        print(
            f"{m:<48} "
            f"{mr['avg_quality_score']:>10.3f} "
            f"{mr['hallucination_rate']:>10.3f} "
            f"{mr['missed_predictions_count']:>8} "
            f"{mr['total_claims']:>7}"
        )
    print("=" * 92)


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.no_gold and args.gold_only:
        parser.error("--no-gold та --gold-only взаємовиключні")
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
