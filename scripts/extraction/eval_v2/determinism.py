# scripts/extraction/eval_v2/determinism.py
from __future__ import annotations

import json

from eval_common.models import EvalCase

from extraction.eval_v2.eval_models import ExtractionResult


def structural_signature(result: ExtractionResult) -> str:
    """Нормалізована структура: список claims, поля без крайніх пробілів, порядок важить.

    Не побайтово: різниця у форматуванні JSON не є недетермінізмом моделі.
    """
    rows = []
    for prediction in result.predictions:
        rows.append(
            {
                "claim_text": prediction.claim_text.strip(),
                "situation": (prediction.situation or "").strip(),
                "topic": prediction.topic.strip(),
            }
        )
    return json.dumps(rows, ensure_ascii=False, sort_keys=True)


def determinism_score(
    first: dict[str, ExtractionResult], second: dict[str, ExtractionResult]
) -> float | None:
    """Частка постів з ідентичним виходом на двох прогонах; None якщо порівнювати нічого."""
    shared = sorted(set(first) & set(second))
    if not shared:
        return None

    identical = 0
    for post_id in shared:
        if structural_signature(first[post_id]) == structural_signature(second[post_id]):
            identical += 1
    return identical / len(shared)


def determinism_subsample(cases: list[EvalCase], size: int = 20) -> list[EvalCase]:
    """Фіксована й спільна для всіх моделей підвибірка: рівномірно по відсортованих id.

    Стратифікація (P5) не потрібна — determinism є non-blocking gate за будь-якого складу
    підвибірки; важлива лише її незмінність між моделями й між прогонами.
    """
    ordered = sorted(cases, key=lambda case: case.id)
    if len(ordered) <= size:
        return ordered

    step = len(ordered) / size
    picked = []
    for index in range(size):
        picked.append(ordered[int(index * step)])
    return picked
