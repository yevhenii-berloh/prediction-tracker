"""Крок 1: збірка датасету extraction eval v2 з трьох каналів.

Окремий запуск і окремий артефакт. Блокується кроком 0 — без живої
Telegram-сесії постів не зібрати.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from eval_common.clients import build_eval_llm  # noqa: E402
from prophet_checker.analysis.extractor import PredictionExtractor  # noqa: E402
from prophet_checker.models.domain import PersonSource, SourceType  # noqa: E402
from prophet_checker.sources.telegram import TelegramSource  # noqa: E402

logger = logging.getLogger(__name__)

# (канал, автор, скільки постів у датасет) — чому саме ці троє, див. дизайн, «Датасет»
CHANNELS = [
    ("@O_Arestovich_official", "Арестович", 70),
    ("@zvizdecmanhustu", "Машовець", 40),
    ("@Analityka_Kush", "Кущ", 40),
]
PREFILTER_MODEL = "gemini/gemini-3.1-flash-lite"
PREFILTER_SHARE = 0.7
CANDIDATE_MULTIPLIER = 4  # скільки постів тягнемо на один потрібний
MIN_CHARS = 300

V1_OUTPUTS = PROJECT_ROOT / "scripts" / "outputs" / "extraction_eval" / "extraction_outputs.json"
OUT_DIR = PROJECT_ROOT / "scripts" / "data" / "extraction"


def load_excluded_v1_ids(path: Path = V1_OUTPUTS) -> set[str]:
    """97 контамінованих постів v1 — з артефакту прогону, а не переписані руками."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    excluded: set[str] = set()
    for per_post in payload["extractions"].values():
        excluded.update(per_post)
    if not excluded:
        raise ValueError(f"{path}: жодного id v1 — список виключень не може бути порожнім")
    return excluded


def post_id_for(channel: str, message_id: int) -> str:
    """Формат id корпусу: O_Arestovich_official_7683."""
    return f"{channel.lstrip('@')}_{message_id}"


def split_strata(
    items: list, prefilter_share: float = PREFILTER_SHARE, seed: int = 42
) -> tuple[list, list]:
    """Ділить відібране на дві strata. Випадкова частина — проти сліпої зони детектора."""
    shuffled = list(items)
    random.Random(seed).shuffle(shuffled)
    cut = round(len(shuffled) * prefilter_share)
    return shuffled[:cut], shuffled[cut:]


def _count_by(posts: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for post in posts:
        key = post[field]
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_payload(
    posts: list[dict], channels: list[str], prefilter_model: str, excluded_ids: set[str]
) -> dict:
    """Артефакт датасету. Список виключень лежить усередині — не лише в дизайні."""
    if not excluded_ids:
        raise ValueError("порожній список виключень: контамінація v1 повернеться непоміченою")

    return {
        "metadata": {
            "collected_at": datetime.now(UTC).date().isoformat(),
            "channels": channels,
            "selection_method": "prefilter+random",
            "prefilter_model": prefilter_model,
            "distribution": {
                "by_author": _count_by(posts, "author"),
                "by_stratum": _count_by(posts, "stratum"),
            },
            "excluded_v1_ids": sorted(excluded_ids),
        },
        "posts": posts,
    }


async def _fetch_candidates(
    source: TelegramSource, channel: str, author: str, limit: int, excluded: set[str]
) -> list[dict]:
    """Сирі кандидати каналу: контаміновані id відсікаються до будь-якого LLM-виклику."""
    person_source = PersonSource(
        id=f"eval-v2-{channel}",
        person_id=author,
        source_type=SourceType.TELEGRAM,
        source_identifier=channel,
    )
    candidates: list[dict] = []
    async for document in source.collect(person_source, limit=limit):
        post_id = post_id_for(channel, int(document.id.rsplit(":", 1)[-1]))
        if post_id in excluded:
            continue
        if len(document.raw_text) < MIN_CHARS:
            continue
        candidates.append(
            {
                "id": post_id,
                "author": author,
                "channel": channel,
                "text": document.raw_text,
                "published_date": document.published_at.date().isoformat(),
                "url": document.url,
            }
        )
    logger.info("channel %s: %d кандидатів", channel, len(candidates))
    return candidates


async def _has_prediction(extractor: PredictionExtractor, post: dict) -> bool:
    predictions = await extractor.extract(
        text=post["text"],
        person_id=post["author"],
        document_id=post["id"],
        person_name=post["author"],
        published_date=post["published_date"],
    )
    return len(predictions) > 0


async def _pick_prefiltered(
    extractor: PredictionExtractor, candidates: list[dict], target: int
) -> tuple[list[dict], list[dict]]:
    """Гонить детектор по кандидатах, доки не набере target позитивних; решта — на random."""
    picked: list[dict] = []
    rest: list[dict] = []
    for index, post in enumerate(candidates):
        if len(picked) >= target:
            rest.extend(candidates[index:])
            break
        if await _has_prediction(extractor, post):
            picked.append(post)
            continue
        rest.append(post)
    return picked, rest


async def collect_channel(
    source: TelegramSource,
    extractor: PredictionExtractor,
    channel_spec: tuple[str, str, int],
    excluded: set[str],
    seed: int,
) -> list[dict]:
    """~70% постів через префільтр-детектор, ~30% випадкових без жодного фільтра."""
    channel, author, target = channel_spec
    candidates = await _fetch_candidates(
        source, channel, author, target * CANDIDATE_MULTIPLIER, excluded
    )
    random.Random(seed).shuffle(candidates)

    prefiltered, rest = await _pick_prefiltered(
        extractor, candidates, round(target * PREFILTER_SHARE)
    )
    n_random = target - len(prefiltered)

    posts = []
    for post in prefiltered:
        posts.append({**post, "stratum": "prefilter"})
    for post in rest[:n_random]:
        posts.append({**post, "stratum": "random"})
    logger.info(
        "channel %s: prefilter=%d random=%d",
        channel,
        len(prefiltered),
        len(posts) - len(prefiltered),
    )
    return posts


async def _collect_all(seed: int, excluded: set[str]) -> list[dict]:
    from telethon import TelegramClient

    from prophet_checker.config import Settings

    settings = Settings()
    client = TelegramClient(
        session=settings.tg_session_path,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    await client.start()
    try:
        source = TelegramSource(client)
        extractor = PredictionExtractor(build_eval_llm(PREFILTER_MODEL, temperature=0))
        posts: list[dict] = []
        for channel_spec in CHANNELS:
            posts.extend(await collect_channel(source, extractor, channel_spec, excluded, seed))
        return posts
    finally:
        await client.disconnect()


async def _main(seed: int, out_path: Path) -> None:
    excluded = load_excluded_v1_ids()
    logger.info("виключено %d контамінованих постів v1", len(excluded))

    posts = await _collect_all(seed, excluded)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    channels = [channel for channel, _, _ in CHANNELS]
    payload = build_payload(posts, channels, PREFILTER_MODEL, excluded)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(posts)} постів → {out_path}")
    print(f"розподіл: {payload['metadata']['distribution']}")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    today = datetime.now(UTC).date().isoformat()
    parser = argparse.ArgumentParser(description="Збірка датасету extraction eval v2")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=OUT_DIR / f"eval_dataset_{today}.json")
    args = parser.parse_args()
    asyncio.run(_main(args.seed, args.out))


if __name__ == "__main__":
    main()
