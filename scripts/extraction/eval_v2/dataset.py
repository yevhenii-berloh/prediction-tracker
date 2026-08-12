# scripts/extraction/eval_v2/dataset.py
from __future__ import annotations

import json
from pathlib import Path

from eval_common.models import EvalCase
from pydantic import ValidationError

from extraction.eval_v2.eval_models import PostInput

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DATASET_PATH = PROJECT_ROOT / "scripts" / "data" / "extraction" / "eval_dataset_2026-08-12.json"

STRATA = ("prefilter", "random")


def load_eval_dataset(path: Path) -> list[EvalCase]:
    """JSON → EvalCase[]. Падає на першому кривому записі, до будь-якого LLM-виклику."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    for raw in payload["posts"]:
        cases.append(_build_case(raw))
    return cases


def _build_case(raw: dict) -> EvalCase:
    post_id = raw.get("id") or "<без id>"
    if not raw.get("id"):
        raise ValueError(f"кривий запис датасету {post_id}: відсутнє поле 'id'")
    try:
        post = PostInput(
            post_id=post_id,
            author=raw["author"],
            channel=raw["channel"],
            text=raw["text"],
            published_date=raw["published_date"],
            stratum=raw["stratum"],
            url=raw.get("url", ""),
        )
    except (KeyError, ValidationError) as exc:
        raise ValueError(f"кривий запис датасету {post_id}: {exc}") from exc

    if post.stratum not in STRATA:
        raise ValueError(f"{post_id}: невідомий stratum {post.stratum!r}, очікується {STRATA}")

    return EvalCase(id=post_id, input=post, labels=None)
