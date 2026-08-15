# scripts/extraction/eval_v2/pool.py
from __future__ import annotations

import logging
import re

from eval_common.judge import Judge
from prophet_checker.models.domain import Prediction

from extraction.eval_v2.eval_models import PooledClaim, PoolResult
from extraction.eval_v2.judge_prompts import (
    CLUSTER_SYSTEM,
    build_cluster_prompt,
    parse_cluster_response,
)

logger = logging.getLogger(__name__)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Ключ точної рівності рядків — не міра схожості. Поріг схожості знято за дизайном."""
    lowered = _PUNCT_RE.sub(" ", text.lower())
    return _SPACE_RE.sub(" ", lowered).strip()


def _absorb(merged: dict[str, dict], prediction: Prediction, model_id: str) -> None:
    key = normalize(prediction.claim_text)
    if not key:
        return
    entry = merged.get(key)
    if entry is None:
        merged[key] = {
            "claim_text": prediction.claim_text,
            "situation": prediction.situation,
            "models": [model_id],
        }
        return
    if model_id not in entry["models"]:
        entry["models"].append(model_id)


def _merge_exact(by_model: dict[str, list[Prediction]]) -> list[dict]:
    """Точні дублікати після нормалізації зливаються в коді, без виклику судді."""
    merged: dict[str, dict] = {}
    for model_id, predictions in by_model.items():
        for prediction in predictions:
            _absorb(merged, prediction, model_id)
    return list(merged.values())


async def _cluster_groups(
    post_text: str, entries: list[dict], judge: Judge
) -> tuple[list[list[int]], bool]:
    """Групи індексів від судді; при збої кожен claim лишається окремим.

    Другим значенням повертає факт збою: сам фолбек безпечний, але reference set
    після нього завищений (дублікати не злились), тож coverage на цьому пості
    рахувати не можна.
    """
    if len(entries) < 2:
        return [[index] for index in range(len(entries))], False

    prompt = build_cluster_prompt(post_text, [entry["claim_text"] for entry in entries])
    try:
        raw = await judge.assess(prompt, system=CLUSTER_SYSTEM)
        return parse_cluster_response(raw), False
    # Кластеризація — перший виклик судді на пості; транспортний збій тут не має
    # вбивати пост, тож ловимо Exception, а не лише помилки розбору.
    except Exception:
        logger.exception("кластеризація не розібралась — claims лишаються окремими")
        return [[index] for index in range(len(entries))], True


def _add_missing_singletons(groups: list[list[int]], total: int) -> list[list[int]]:
    """Індекс, якого суддя не згадав, не має зникнути з пулу."""
    seen: set[int] = set()
    for group in groups:
        seen.update(group)

    complete = [group for group in groups if group]
    for index in range(total):
        if index not in seen:
            complete.append([index])
    return complete


def _models_of(members: list[dict]) -> list[str]:
    models: list[str] = []
    for member in members:
        for model_id in member["models"]:
            if model_id not in models:
                models.append(model_id)
    return models


async def pool_claims(
    post_id: str,
    post_text: str,
    by_model: dict[str, list[Prediction]],
    judge: Judge,
) -> PoolResult:
    """Claims усіх учасників по одному посту → множина унікальних claims (крок 3)."""
    entries = _merge_exact(by_model)
    if not entries:
        return PoolResult(claims=[], clustering_failed=False)

    raw_groups, clustering_failed = await _cluster_groups(post_text, entries, judge)
    groups = _add_missing_singletons(raw_groups, len(entries))

    claims: list[PooledClaim] = []
    for position, group in enumerate(groups):
        members = [entries[index] for index in group if 0 <= index < len(entries)]
        if not members:
            continue
        claims.append(
            PooledClaim(
                claim_id=f"{post_id}:{position}",
                claim_text=members[0]["claim_text"],
                situation=members[0]["situation"],
                models=_models_of(members),
            )
        )
    return PoolResult(claims=claims, clustering_failed=clustering_failed)
