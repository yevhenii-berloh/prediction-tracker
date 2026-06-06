"""Будує набір для ручної оцінки екстракції: 50 постів з передбаченнями (з БД) +
50 без (точковий прогін extractor на all.json) → JSON з порожніми score/note."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from prophet_checker.analysis.extractor import PredictionExtractor  # noqa: E402
from prophet_checker.config import Settings  # noqa: E402
from prophet_checker.llm.client import LLMClient  # noqa: E402
from prophet_checker.models.db import PredictionDB, RawDocumentDB  # noqa: E402

ALL_POSTS = PROJECT_ROOT / "scripts" / "data" / "arestovich" / "all.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "scripts" / "outputs" / "annotation" / "annotation_set.json"
DEFAULT_MODEL = "gemini/gemini-3.1-flash-lite-preview"

PROVIDER_API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def post_url(post_id: str) -> str:
    if post_id.startswith("tg:"):
        channel, _, msg = post_id[3:].rpartition(":")
    else:
        channel, _, msg = post_id.rpartition("_")
    return f"https://t.me/{channel.lstrip('@')}/{msg}"


def _claim_entry(p) -> dict:
    return {
        "claim_text": p.claim_text,
        "situation": p.situation,
        "prediction_date": p.prediction_date.isoformat() if p.prediction_date else None,
        "target_date": p.target_date.isoformat() if p.target_date else None,
        "topic": p.topic,
        "claim_score": None,
        "claim_note": "",
    }


def _post_entry(post_id: str, published_at, has_predictions: bool, source: str, claims: list) -> dict:
    return {
        "post_id": post_id,
        "url": post_url(post_id),
        "published_at": published_at,
        "has_predictions": has_predictions,
        "source": source,
        "post_score": None,
        "post_note": "",
        "claims": claims,
    }


async def load_db_positives(session_factory, n: int, min_chars: int, seed: int) -> list[dict]:
    async with session_factory() as session:
        docs = (await session.execute(
            select(RawDocumentDB)
            .where(func.length(RawDocumentDB.raw_text) >= min_chars)
            .where(RawDocumentDB.id.in_(select(PredictionDB.document_id).distinct()))
        )).scalars().all()
        chosen = random.Random(seed).sample(list(docs), min(n, len(docs)))
        result = []
        for doc in chosen:
            preds = (await session.execute(
                select(PredictionDB).where(PredictionDB.document_id == doc.id)
            )).scalars().all()
            published = doc.published_at.date().isoformat() if doc.published_at else None
            result.append(_post_entry(doc.id, published, True, "db", [_claim_entry(p) for p in preds]))
        return result


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


async def collect_extractor_negatives(
    posts, extractor, n: int, min_chars: int, seed: int, max_extractions: int, exclude_urls: set
) -> list[dict]:
    eligible = [
        p for p in posts
        if p.get("person_name") == "Арестович"
        and len(p.get("text", "")) >= min_chars
        and post_url(p["id"]) not in exclude_urls
    ]
    random.Random(seed).shuffle(eligible)
    negatives, tried = [], 0
    for p in eligible:
        if len(negatives) >= n or tried >= max_extractions:
            break
        tried += 1
        try:
            preds = await extractor.extract(
                text=p["text"], person_id=p["person_name"], document_id=p["id"],
                person_name=p["person_name"], published_date=p["published_at"],
            )
        except Exception as exc:
            print(f"  skip {p['id']}: {type(exc).__name__}: {exc}", flush=True)
            continue
        if not preds:
            published = str(p["published_at"])[:10]
            negatives.append(_post_entry(p["id"], published, False, "extractor_pool", []))
        print(f"  [neg {len(negatives)}/{n}] tried={tried} {p['id']}: {len(preds)} claims", flush=True)
    return negatives


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--min-chars", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-extractions", type=int, default=300)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--created", default=date.today().isoformat())
    args = parser.parse_args()

    n_pos = args.n // 2
    n_neg = args.n - n_pos

    engine = create_async_engine(Settings().database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    positives = await load_db_positives(session_factory, n_pos, args.min_chars, args.seed)
    await engine.dispose()
    print(f"positives from DB: {len(positives)}/{n_pos}")
    if len(positives) < n_pos:
        print(f"  WARN: лише {len(positives)} позитивів у БД (треба {n_pos})")

    posts = json.load(open(ALL_POSTS))
    exclude = {p["url"] for p in positives}
    extractor = build_extractor(args.model)
    negatives = await collect_extractor_negatives(
        posts, extractor, n_neg, args.min_chars, args.seed, args.max_extractions, exclude)
    print(f"negatives via extractor: {len(negatives)}/{n_neg}")
    if len(negatives) < n_neg:
        print(f"  WARN: лише {len(negatives)} негативів (пул/кеп вичерпано)")

    all_posts = positives + negatives
    random.Random(args.seed).shuffle(all_posts)
    out = {
        "meta": {
            "created": args.created,
            "model": args.model,
            "n": len(all_posts),
            "with_predictions": len(positives),
            "without_predictions": len(negatives),
            "seed": args.seed,
            "min_chars": args.min_chars,
        },
        "posts": all_posts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"saved → {args.output}  ({len(all_posts)} posts: {len(positives)} pos / {len(negatives)} neg)")


if __name__ == "__main__":
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    asyncio.run(main())
