# scripts/extraction/eval_v2/judge_prompts.py
from __future__ import annotations

import json
import re

from prophet_checker.llm.prompts import get_extraction_system

from extraction.eval_v2.eval_models import ClaimVerdict, MissedClaim

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)

CLUSTER_SYSTEM = (
    "Ти групуєш твердження, витягнуті різними моделями з ОДНОГО поста. "
    "Два твердження потрапляють в одну групу тільки якщо вони стверджують те саме "
    "передбачення. Заперечення, протилежний напрямок і інший часовий обрій — це РІЗНІ "
    "твердження, навіть якщо формулювання майже збігаються. "
    "Відповідаєш ЛИШЕ валідним JSON."
)

CHECK_SYSTEM = (
    "Ти — суворий перевіряльник. Відповідаєш на перевірні питання про ОДНЕ твердження "
    "проти тексту поста. Кожне питання має однозначну відповідь; не оцінюй, наскільки "
    "добре твердження сформульоване. Відповідаєш ЛИШЕ валідним JSON."
)

MISSED_SYSTEM = (
    "Ти шукаєш у тексті поста передбачення, яких немає в поданому списку. "
    "Додаєш лише те, що проходить рубрику. Відповідаєш ЛИШЕ валідним JSON."
)


def _extract_json(text: str) -> dict:
    """Перший JSON-обʼєкт відповіді; проза після нього ігнорується."""
    match = _FENCE_RE.match(text.strip())
    payload = (match.group(1) if match else text).strip()
    start = payload.find("{")
    if start > 0:
        payload = payload[start:]
    try:
        data, _ = json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"непарсибельна відповідь судді: {exc}") from exc
    return data


def build_cluster_prompt(post_text: str, claim_texts: list[str]) -> str:
    numbered = []
    for index, claim in enumerate(claim_texts):
        numbered.append(f"{index}. {claim}")
    return (
        "Згрупуй твердження, які говорять те саме передбачення. Кожен індекс має "
        "потрапити рівно в одну групу; твердження без пари утворює групу з одного "
        'елемента. Формат: {"clusters": [[0, 2], [1]]}\n\n'
        f"ПОСТ:\n{post_text}\n\nТВЕРДЖЕННЯ:\n" + "\n".join(numbered)
    )


def parse_cluster_response(text: str) -> list[list[int]]:
    data = _extract_json(text)
    clusters: list[list[int]] = []
    for group in data.get("clusters", []):
        clusters.append([int(index) for index in group])
    return clusters


def build_check_prompt(post_text: str, claim_text: str, situation: str | None) -> str:
    return (
        "Дай відповідь на чотири питання про ТВЕРДЖЕННЯ проти ПОСТА:\n"
        "1) claim_grounded — чи простежується твердження до тексту поста;\n"
        "2) situation_grounded — чи простежується до тексту поле СИТУАЦІЯ "
        "(це парафраз, дослівного збігу не буде);\n"
        "3) passes_rubric — чи це передбачення за поданою РУБРИКОЮ;\n"
        "4) truncated — чи твердження обірване на півслові.\n"
        'Формат: {"claim_grounded": true, "situation_grounded": true, '
        '"passes_rubric": true, "truncated": false, "reason": "коротко"}\n\n'
        f"РУБРИКА:\n{get_extraction_system()}\n\n"
        f"ПОСТ:\n{post_text}\n\n"
        f"ТВЕРДЖЕННЯ:\n{claim_text}\n\n"
        f"СИТУАЦІЯ:\n{situation or '—'}"
    )


def parse_check_response(text: str, claim_id: str) -> ClaimVerdict:
    data = _extract_json(text)
    return ClaimVerdict(
        claim_id=claim_id,
        claim_grounded=bool(data["claim_grounded"]),
        situation_grounded=bool(data["situation_grounded"]),
        passes_rubric=bool(data["passes_rubric"]),
        truncated=bool(data.get("truncated", False)),
        reason=data.get("reason", ""),
    )


def build_missed_prompt(post_text: str, claim_texts: list[str]) -> str:
    listed = "\n".join(f"- {claim}" for claim in claim_texts) or "— (нічого не витягнуто)"
    return (
        "Які передбачення є в ПОСТІ, але відсутні у ВЖЕ ЗНАЙДЕНОМУ? "
        "Якщо таких немає — поверни порожній список. "
        'Формат: {"missed": [{"text": "...", "reason": "..."}]}\n\n'
        f"РУБРИКА:\n{get_extraction_system()}\n\n"
        f"ПОСТ:\n{post_text}\n\nВЖЕ ЗНАЙДЕНЕ:\n{listed}"
    )


def parse_missed_response(text: str) -> list[MissedClaim]:
    data = _extract_json(text)
    missed: list[MissedClaim] = []
    for item in data.get("missed", []):
        missed.append(MissedClaim(text=item["text"], reason=item.get("reason", "")))
    return missed
