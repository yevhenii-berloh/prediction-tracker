# scripts/generation/judge_prompts.py
from __future__ import annotations

import json
import re

from generation.gen_models import ClaimVerdict
from prophet_checker.llm.prompts import render_predictions
from prophet_checker.models.domain import RetrievedPrediction

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)

FAITHFULNESS_SYSTEM = (
    "Ти — суворий фактчекер. Розкладаєш ВІДПОВІДЬ на атомарні фактичні твердження "
    "й перевіряєш кожне проти наданих ДЖЕРЕЛ. Відповідаєш ЛИШЕ валідним JSON."
)
COMPLETENESS_SYSTEM = (
    "Визначаєш, чи конкретне ТВЕРДЖЕННЯ відображене у ВІДПОВІДІ. Відповідаєш ЛИШЕ JSON."
)
CITATION_SYSTEM = (
    "Ти — прискіпливий рецензент. Тобі дають одне РЕЧЕННЯ з відповіді та ОДНЕ ДЖЕРЕЛО, "
    "на яке це речення посилається. Кажеш, чи підтверджує саме це джерело саме це речення. "
    "Статус прогнозу в джерелі є авторитетним щодо того, справдився він чи ні. "
    "Відповідаєш ЛИШЕ валідним JSON."
)


def _extract_json(text: str) -> dict:
    m = _FENCE_RE.match(text.strip())
    payload = m.group(1) if m else text
    return json.loads(payload)


def build_faithfulness_prompt(answer: str, sources: list) -> str:
    # render_predictions — той самий рендер, що бачить генератор (build_rag_prompt),
    # тож суддя оцінює відповідь проти ТОТОЖНОГО джерела (без сліпоти до date/target/confidence)
    return (
        "Розклади ВІДПОВІДЬ на атомарні фактичні твердження. Для кожного визнач, чи воно "
        "підкріплене ДЖЕРЕЛАМИ (supported true/false). "
        "У кожного джерела є поле status (confirmed/refuted/unresolved/premature) — це "
        "АВТОРИТЕТНИЙ вердикт щодо прогнозу. Твердження про результат (прогноз справдився / "
        "не справдився / ще зарано судити) вважай supported, якщо воно ВІДПОВІДАЄ полю status, "
        "і not supported, лише якщо СУПЕРЕЧИТЬ йому. "
        "Якщо ВІДПОВІДЬ — відмова або не містить "
        'фактів, поверни порожній список. Формат: {"claims": [{"claim": "...", '
        '"supported": true, "reason": "..."}]}\n\n'
        f"ВІДПОВІДЬ:\n{answer}\n\nДЖЕРЕЛА:\n{render_predictions(sources)}"
    )


def build_completeness_prompt(answer: str, claim: str, situation: str | None = None) -> str:
    ctx = (
        "\n\nКОНТЕКСТ (ситуація прогнозу — лише щоб правильно зрозуміти ТВЕРДЖЕННЯ; "
        f"переказувати її не треба):\n{situation}"
        if situation
        else ""
    )
    return (
        "Чи ВІДПОВІДЬ відображає (згадує або передає суть) ТВЕРДЖЕННЯ? "
        'Формат: {"covered": true|false, "reason": "..."}\n\n'
        f"ТВЕРДЖЕННЯ:\n{claim}{ctx}\n\nВІДПОВІДЬ:\n{answer}"
    )


def parse_faithfulness_response(text: str) -> list[ClaimVerdict]:
    data = _extract_json(text)
    return [
        ClaimVerdict(claim=c["claim"], supported=bool(c["supported"]), reason=c.get("reason", ""))
        for c in data.get("claims", [])
    ]


def parse_completeness_response(text: str) -> tuple[bool, str]:
    data = _extract_json(text)
    return bool(data["covered"]), data.get("reason", "")


def build_citation_prompt(sentence: str, source: RetrievedPrediction) -> str:
    # render_predictions — той самий рендер, що бачить генератор, тож суддя оцінює
    # посилання проти ТОТОЖНОГО джерела
    return (
        "Чи підтверджує подане джерело твердження в реченні? "
        'Формат: {"supported": true/false, "reason": "коротко"}\n\n'
        f"РЕЧЕННЯ:\n{sentence}\n\nДЖЕРЕЛО:\n{render_predictions([source])}"
    )


def parse_citation_response(text: str) -> tuple[bool, str]:
    data = _extract_json(text)
    return bool(data.get("supported", False)), data.get("reason", "")
