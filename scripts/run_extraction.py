"""Стадія 2/3: прогін PredictionExtractor (Flash Lite) на обраних постах →
витягнуті клейми (claim_text / situation / dates / topic) у extracted_claims.json."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from prophet_checker.analysis.extractor import PredictionExtractor
from prophet_checker.llm.client import LLMClient

DEFAULT_MODEL = "gemini/gemini-3.1-flash-lite-preview"
RUN_DIR = PROJECT_ROOT / "scripts" / "outputs" / "pipeline_run"
DEFAULT_INPUT = RUN_DIR / "selected_posts.json"
DEFAULT_OUTPUT = RUN_DIR / "extracted_claims.json"

PROVIDER_API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def build_extractor(model_id: str) -> PredictionExtractor:
    provider, model = model_id.split("/", 1)
    env_var = PROVIDER_API_KEY_ENV.get(provider)
    if not env_var:
        raise ValueError(f"Unknown provider {provider!r}")
    api_key = os.environ.get(env_var)
    if not api_key:
        raise RuntimeError(f"Missing API key for {provider!r}: set {env_var}")
    client = LLMClient(provider=provider, model=model, api_key=api_key, temperature=0.0)
    return PredictionExtractor(client)


def serialize_claim(p) -> dict:
    return {
        "claim_text": p.claim_text,
        "situation": p.situation,
        "prediction_date": p.prediction_date.isoformat() if p.prediction_date else None,
        "target_date": p.target_date.isoformat() if p.target_date else None,
        "topic": p.topic,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sleep", type=float, default=0.0)
    args = parser.parse_args()

    posts = json.load(open(args.input))
    extractor = build_extractor(args.model)

    extractions = []
    total_claims = 0
    for i, post in enumerate(posts, 1):
        preds = await extractor.extract(
            text=post["text"],
            person_id=post["person_name"],
            document_id=post["id"],
            person_name=post["person_name"],
            published_date=post["published_at"],
        )
        claims = [serialize_claim(p) for p in preds]
        total_claims += len(claims)
        extractions.append({
            "post_id": post["id"],
            "post_published_at": post["published_at"],
            "post_text": post["text"],
            "claims": claims,
        })
        print(f"  [{i}/{len(posts)}] {post['id']}: {len(claims)} claims", flush=True)
        if args.sleep > 0:
            await asyncio.sleep(args.sleep)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump({
            "model": args.model,
            "posts_processed": len(posts),
            "claims_total": total_claims,
            "extractions": extractions,
        }, f, ensure_ascii=False, indent=2)

    posts_with_claims = sum(1 for e in extractions if e["claims"])
    print(f"\nposts: {len(posts)}  with claims: {posts_with_claims}  total claims: {total_claims}")
    print(f"saved → {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
