from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from prophet_checker.config import Settings
from prophet_checker.llm import EmbeddingClient
from retrieval.eval_store import PostgresEvalEmbStore

REPRESENTATIONS = ("claim_text", "situation", "claim_situation")

CORPUS_PATH = Path("scripts/data/retrieval_eval_corpus.json")

# Кандидати фіналізуються screening'ом по MMTEB UK/RU; baseline лишається першим.
MODELS = ["text-embedding-3-small"]


def build_representation_text(row: dict, kind: str) -> str | None:
    """Текст для ембедингу. None → прогноз пропускається в цій репрезентації."""
    claim = row["claim_text"]
    situation = (row.get("situation") or "").strip()
    if kind == "claim_text":
        return claim
    if kind == "situation":
        return situation or None
    if kind == "claim_situation":
        return f"{claim}\n{situation}" if situation else claim
    raise ValueError(f"unknown representation: {kind}")


def config_name(model: str, kind: str) -> str:
    return f"{model}__{kind}"


async def run_sweep(corpus: list[dict], configs, embedder_factory, store) -> None:
    """configs: список (model, representation_kind). embedder_factory(model) → обʼєкт з .embed."""
    await store.ensure_table()
    for model, kind in configs:
        name = config_name(model, kind)
        await store.recreate(name)
        embedder = embedder_factory(model)
        for row in corpus:
            text_ = build_representation_text(row, kind)
            if text_ is None:
                continue
            vector = await embedder.embed(text_)
            await store.add(name, row["id"], vector)


async def run(corpus_path: Path, models: list[str]) -> None:
    settings = Settings()
    corpus = json.loads(corpus_path.read_text())
    configs = [(m, k) for m in models for k in REPRESENTATIONS]

    def factory(model: str):
        return EmbeddingClient(model=model, api_key=settings.openai_api_key)

    async with AsyncExitStack() as stack:
        engine = create_async_engine(settings.database_url, echo=False)
        stack.push_async_callback(engine.dispose)
        store = PostgresEvalEmbStore(async_sessionmaker(engine, expire_on_commit=False))
        await run_sweep(corpus, configs, factory, store)
    print(f"sweep done: {len(configs)} configs × {len(corpus)} predictions")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    parser.add_argument("--models", nargs="+", default=MODELS)
    args = parser.parse_args()
    asyncio.run(run(args.corpus, args.models))


if __name__ == "__main__":
    main()
