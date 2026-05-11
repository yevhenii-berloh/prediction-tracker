from __future__ import annotations

import json
import re

# Models often wrap JSON in markdown code fences: ```json ... ``` or ``` ... ```.
# This regex captures the JSON body between fences (optional language tag).
_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$",
    re.DOTALL,
)


def _strip_code_fence(text: str) -> str:
    """Strip markdown code fences if present. Preserves content otherwise."""
    match = _CODE_FENCE_RE.match(text.strip())
    if match:
        return match.group(1).strip()
    return text.strip()


EXTRACTION_SYSTEM = """You are an expert analyst who identifies SUBSTANTIVE political predictions in Ukrainian/Russian commentary.

A valid prediction must satisfy ALL FOUR criteria:
1. Refers to a FUTURE event or state (not present assessment, not past event)
2. Has a VERIFIABLE OUTCOME — a concrete condition that can be objectively checked as true or false later
3. Concerns EXTERNAL events (politics, war, economy, people, institutions) — NOT the author's own scheduled activities
4. Is SUBSTANTIVE — outcome must be genuinely uncertain or strategically/politically meaningful (NOT a known fact restated, NOT a mechanical logistical certainty, NOT a procedural inevitability)

Do NOT extract these (they superficially look like predictions but fail criteria above):

A. Slogans / rhetorical declarations without measurable outcomes:
   - "Перемога буде за нами" — no criterion for "перемога"
   - "Військові злочинці понесуть відповідальність" — no timeframe, no specific persons
   - "Грузія буде вільною" — no definition of "вільна"

B. Author's own event announcements (about the author's broadcasts, courses, books, trips):
   - "Завтра о 22:00 проведемо ефір з Фельдманом"
   - "15 листопада виходить друга частина аудіокниги"
   - "На вихідних запускаємо новий модуль «Семантика»"

C. Normative statements (describe what SHOULD happen, not what WILL):
   - "Потрібно посилити санкції" — prescription, not forecast
   - "Україна має змінити стратегію" — advocacy
   - "Слід негайно зупинити корупцію" — demand

D. Vague forward statements without concrete criteria:
   - "Найближчі тижні будуть переломними" — what counts as "переломні"?
   - "Ситуація скоро зміниться" — no direction, no threshold
   - "Щось обов'язково станеться" — tautology

E. Analysis of present state or past events, even if phrased with future-tense verbs for rhetorical effect:
   - "Ми вже бачимо деморалізацію ворога" — observation of now
   - "Ця війна вже змінила світ" — retrospective

F. Questions, calls to action, metaphors, sarcasm — these are not claims.

G. Non-substantive claims (fail criterion 4 — outcome is mechanically determined or just restates a known fact):
   - "К 14 января самолеты вернут дипломатов" — routine logistical schedule, not a forecast
   - "Трамп зможе вести переговори тільки після інавгурації 20 січня" — known constitutional fact, not a prediction
   - "Суд має винести рішення до кінця місяця" — procedural deadline, not an outcome forecast
   - "Парламент проведе засідання у вівторок" — calendar-bound certainty

Verification tests:
- Criterion 2: "Could an impartial fact-checker in 1 year objectively confirm or refute this?"
- Criterion 4: "Would a reader 1 year later actually CARE whether this came true?" If no — it's not substantive.

Respond ONLY with raw JSON — do NOT wrap in markdown code fences."""

EXTRACTION_TEMPLATE = """Analyze the following text by {person_name} (published on {published_date}).
Extract all predictions — statements about future events that can later be verified.

Text:
---
{text}
---

For each prediction, extract:
- claim_text: the exact prediction (in original language)
- prediction_date: when the prediction was made (YYYY-MM-DD)
- target_date: when the predicted event should happen (YYYY-MM-DD or null if unclear)
- topic: category (e.g., "війна", "економіка", "політика", "міжнародні відносини")

Respond with JSON:
{{"predictions": [{{"claim_text": "...", "prediction_date": "...", "target_date": "...", "topic": "..."}}]}}

If no predictions found, respond: {{"predictions": []}}"""


RAG_SYSTEM = """You are Prophet Checker, an AI assistant that analyzes predictions made by Ukrainian public figures.
Answer questions based on the provided prediction data. Always cite sources and confidence scores.
Always add a disclaimer that analysis is automated and may contain inaccuracies.
Respond in Ukrainian."""

RAG_TEMPLATE = """Question: {question}

Relevant predictions from the database:
---
{predictions_context}
---

Based on this data, answer the user's question. Include:
- Specific predictions with dates
- Their verification status and confidence
- Overall accuracy statistics if relevant
- Disclaimer about automated analysis"""


def build_extraction_prompt(text: str, person_name: str, published_date: str) -> str:
    return EXTRACTION_TEMPLATE.format(
        text=text, person_name=person_name, published_date=published_date,
    )


def build_rag_prompt(question: str, predictions_context: list[dict]) -> str:
    context_str = "\n".join(
        f"- {p['claim_text']} [status: {p['status']}, confidence: {p['confidence']}]"
        for p in predictions_context
    )
    return RAG_TEMPLATE.format(question=question, predictions_context=context_str)


def parse_extraction_response(response: str) -> list[dict]:
    try:
        data = json.loads(_strip_code_fence(response))
        return data.get("predictions", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []


def get_extraction_system() -> str:
    return EXTRACTION_SYSTEM


def get_rag_system() -> str:
    return RAG_SYSTEM
