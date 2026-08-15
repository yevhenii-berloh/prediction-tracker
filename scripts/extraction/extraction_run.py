from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from extraction.detection_eval import (
    PROVIDER_API_KEY_ENV,
    MIN_CALL_INTERVAL_SECONDS,
)
from prophet_checker.analysis.extractor import PredictionExtractor
from prophet_checker.llm.client import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini/gemini-3.1-flash-lite-preview"
SAMPLE_POSTS_PATH = PROJECT_ROOT / "scripts" / "data" / "raw" / "samples" / "sample_posts.json"
V1_EXTRACTIONS_PATH = PROJECT_ROOT / "scripts" / "outputs" / "extraction_eval" / "extraction_outputs.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "scripts" / "outputs" / "verification_eval" / "v2_extraction_outputs.json"


def select_posts_for_v2(posts: list[dict], v1_extractions: dict, model_id: str, author: str) -> list[dict]:
    v1_model_outputs = v1_extractions.get("extractions", {}).get(model_id, {})
    target_post_ids = {pid for pid, claims in v1_model_outputs.items() if claims}
    return [p for p in posts if p["id"] in target_post_ids and p["person_name"] == author]


def build_extractor(model_id: str) -> PredictionExtractor:
    if "/" not in model_id:
        raise ValueError(f"model_id must be 'provider/model', got {model_id!r}")
    provider, model = model_id.split("/", 1)
    env_var = PROVIDER_API_KEY_ENV.get(provider)
    if not env_var:
        raise ValueError(f"Unknown provider {provider!r}")
    api_key = os.environ.get(env_var)
    if not api_key:
        raise RuntimeError(f"Missing API key for {provider!r}: set {env_var}")
    client = LLMClient(provider=provider, model=model, api_key=api_key, temperature=0.0)
    return PredictionExtractor(client)


def serialize_v2_prediction(p) -> dict:
    return {
        "claim_text": p.claim_text,
        "situation": p.situation,
        "prediction_date": p.prediction_date.isoformat() if p.prediction_date else None,
        "target_date": p.target_date.isoformat() if p.target_date else None,
        "topic": p.topic,
    }


async def run_extraction(model_id: str, posts: list[dict], min_interval: float) -> tuple[list[dict], dict]:
    extractor = build_extractor(model_id)
    extractions: list[dict] = []
    total_kept = 0

    for post in posts:
        outcome = await extractor.extract(
            text=post["text"],
            person_id=post["person_name"],
            document_id=post["id"],
            person_name=post["person_name"],
            published_date=post["published_at"],
        )
        claims = [serialize_v2_prediction(p) for p in outcome.predictions]
        total_kept += len(claims)
        extractions.append({
            "post_id": post["id"],
            "post_published_at": post["published_at"],
            "post_text": post["text"],
            "claims": claims,
        })
        if min_interval > 0:
            await asyncio.sleep(min_interval)

    stats = {
        "posts_processed": len(posts),
        "claims_kept": total_kept,
    }
    return extractions, stats


def save_artifact(extractions: list[dict], stats: dict, model_id: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "metadata": {
            "model": model_id,
            "prompt_version": "v2",
            "run_at": datetime.now(timezone.utc).isoformat(),
            **stats,
        },
        "extractions": extractions,
    }
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def main_async(args: argparse.Namespace) -> None:
    posts = json.loads(SAMPLE_POSTS_PATH.read_text(encoding="utf-8"))
    v1_extractions = json.loads(V1_EXTRACTIONS_PATH.read_text(encoding="utf-8"))
    selected = select_posts_for_v2(posts, v1_extractions, args.model, args.author)
    if args.limit:
        selected = selected[: args.limit]
    print(f"V2 extraction model: {args.model}")
    print(f"Selected {len(selected)} posts (author={args.author})")

    min_interval = MIN_CALL_INTERVAL_SECONDS.get(args.model, 0.0)
    if min_interval > 0:
        est_min = len(selected) * min_interval / 60
        print(f"Throttle: {min_interval}s/call → ~{est_min:.1f} min")

    extractions, stats = await run_extraction(args.model, selected, min_interval)
    print(f"Stats: {stats}")
    save_artifact(extractions, stats, args.model, args.output)
    print(f"Saved → {args.output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 extraction run (Task 19.8b Stage 1)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--author", default="Арестович")
    parser.add_argument("--limit", type=int, default=0, help="Process first N posts (0=all)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
