from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

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

RECONSTRUCTION & FAITHFULNESS (how to phrase each extracted claim):

R1. Self-contained form. Each claim_text must be a standalone, grammatical,
    falsifiable sentence written in Ukrainian. Do NOT output bare
    list items, fragments, or noun phrases. Do NOT keep list punctuation
    (";", "—", trailing commas).

R2. Enumerated forecasts. When a forecast is given as a bulleted/numbered
    list, do NOT emit one claim per raw bullet. Reconstruct: either fold the
    list into a single higher-level claim, or restate the substantive items
    as full sentences — whichever faithfully captures what the author claims.

R3. Preserve the author's stance and polarity. Capture WHOSE action is
    predicted and WHETHER the author forecasts it will HAPPEN or FAIL. If the
    author lists the steps of a process they predict will FAIL, the
    prediction is the FAILURE of that process — do NOT extract each step as
    if the author forecasts its success.

EXAMPLE (enumerated agenda the author predicts will fail):
Source: "Ожидаемые вехи на пути комиссии Ермак-Козак: — прекращение огня;
— вода в Крым; — выборы в ОРДЛО... Поэтому, я думаю что у Путина-Зеленского
не получится."
WRONG -> ["прекращение огня;", "вода в Крым;", "выборы в ОРДЛО;"]
        (fragments; inverted polarity — author predicts these will NOT happen)
RIGHT -> "Спроба поетапного примирення з РФ через комісію Єрмак–Козак
        (припинення вогню, вода в Крим, вибори в ОРДЛО) зрештою провалиться."

Respond ONLY with raw JSON — do NOT wrap in markdown code fences."""

EXTRACTION_TEMPLATE = """Analyze the following text by {person_name} (published on {published_date}).
Extract all predictions — statements about future events that can later be verified.

Text:
---
{text}
---

For each prediction, extract:
- claim_text: a SELF-CONTAINED reconstruction of the prediction, written in
  Ukrainian (translate if the post is in Russian). Rewrite it as one complete, grammatical,
  falsifiable sentence — explicit subject + predicate + timeframe when known.
  Never copy a bare list item or fragment; never keep list punctuation. The
  sentence must state the AUTHOR'S OWN forecast with its correct polarity
  (whether the author expects the event to HAPPEN or to FAIL / NOT happen).
- prediction_date: when the prediction was made (YYYY-MM-DD)
- target_date: when the predicted event should happen (YYYY-MM-DD or null if unclear)
- topic: category (e.g., "війна", "економіка", "політика", "міжнародні відносини")
- situation: 1-2 sentences (in the post's language) summarizing the
  events or circumstances the author was responding to when making
  this prediction. Answer "in response to what situation was this
  forecast made?". Synthesize from the whole post — capture preceding
  setup, triggering events, persons involved. This is YOUR summary,
  NOT a verbatim quote.

Respond with JSON:
{{"predictions": [{{"claim_text": "...", "prediction_date": "...", "target_date": "...", "topic": "...", "situation": "..."}}]}}

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


VERIFICATION_SYSTEM_V2 = """You are a fact-checker who verifies political/economic predictions about Ukraine
and global events. Today's date is {today}. The prediction was made on a past
date — your job is to assess whether it can be evaluated NOW, and if so, what
the verdict is.

Determine EIGHT outputs (all required in JSON response):

═══════════════════════════════════════════════════════════════════
1) status — exactly one of:

   "confirmed" — the predicted event happened as foretold. You have
                concrete evidence. The prediction's timeframe (target_date,
                or reasonable interpretation) has passed.

   "refuted"  — the predicted event did NOT happen, OR the opposite occurred.
                Concrete evidence required. Timeframe has passed.

   "unresolved" — the predicted event's timeframe has passed, but evidence is
                  ambiguous, the claim is too vague to falsify, or no public
                  record exists. Re-checking later WON'T help — this is a
                  permanent verdict.

   "premature" — the predicted event has not yet occurred but is still
                 POSSIBLE. The timeframe hasn't elapsed, OR the trigger
                 condition (for conditional predictions like "if X happens")
                 hasn't fired. We should retry verification later.

2) confidence — 0.0 to 1.0
   Your certainty in the verdict.

3) prediction_strength — assess the CLAIM ITSELF (independent of outcome):

   "high"   — concrete falsifiable claim with measurable outcome.
   "medium" — probabilistic but substantive claim with clear outcome.
   "low"    — vague hedge, possibility statement, or non-substantive forecast.
4) prediction_value — assess the IMPORTANCE/RESONANCE of the predicted outcome.
   Even in consequential topics (war, geopolitics), distinguish:

   "high"   — outcome reshapes a country, region, or balance of power.
              Examples: "війна закінчиться у 2026", "Україна стане
              федеральним округом", "Захід вступить у війну з РФ".
              NOT high: process announcements, logistical events,
              announcements of intent within an ongoing conflict.
   "medium" — affects a sector, region, institution, or specific subgroup;
              significant policy/military escalation but not regime-changing.
              Examples: "новий уряд буде сформований", "будуть нові санкції",
              "поставки зброї будуть розширені".
   "low"    — process/logistical/descriptive within a larger context;
              tautology; calendar-bound certainty; announcement of intent
              (not outcome); description of ongoing activity; vague slogan.
              Examples: "дипломати зустрінуться", "позиції політиків
              змінюватимуться залежно від подій", "сторони нарабатывают
              соглашения", "45 евакуаційних автобусів поїдуть з міста".
5) reasoning — 1-3 sentences
   Explain the verdict, strength, and value assessment.

6) evidence — concrete fact text or null
   REQUIRED when status=confirmed/refuted. May be null when premature/unresolved.
   Do NOT include URLs (you have no web access).

7) retry_after — YYYY-MM-DD or null
   REQUIRED when status=premature. Null for all other statuses.

8) max_horizon — YYYY-MM-DD or null
   Set ONLY if status="premature" AND target_date is null. Otherwise null.

═══════════════════════════════════════════════════════════════════
MUTUAL EXCLUSION RULES (strictly enforce):
- status=confirmed/refuted → evidence MUST be a concrete fact, retry_after=null
- status=unresolved → retry_after=null
- status=premature → retry_after MUST be a date, evidence may be null
- max_horizon set ONLY when status=premature AND target_date=null

Respond ONLY with raw JSON, no markdown fences:

{{
  "status": "confirmed" | "refuted" | "unresolved" | "premature",
  "confidence": 0.0 to 1.0,
  "prediction_strength": "low" | "medium" | "high",
  "prediction_value": "low" | "medium" | "high",
  "reasoning": "1-3 sentences",
  "evidence": "concrete fact text or null. Do NOT include URLs.",
  "retry_after": "YYYY-MM-DD or null",
  "max_horizon": "YYYY-MM-DD or null"
}}"""


VERIFICATION_TEMPLATE_V2 = """Claim: "{claim}"
Made on: {prediction_date}
Expected by: {target_date}

Situation that prompted the claim:
---
{situation}
---

Today: {today}.

Provide your verdict per the rubric."""


ASSESSMENT_SYSTEM_V2 = """You assess two INDEPENDENT properties of a political/economic prediction about
Ukraine and global events. Today's date is {today}. You do NOT judge whether the
prediction came true — only how it is phrased and how much its outcome matters.

═══════════════════════════════════════════════════════════════════
prediction_strength and prediction_value are INDEPENDENT axes:
   - strength = HOW the claim is phrased (vague ↔ precise)
   - value    = HOW MUCH the outcome matters (trivial ↔ world-changing)
   A vague claim about war ending = strength:low + value:high.
   A precise claim about a diplomat's schedule = strength:high + value:low.

Determine THREE outputs (all required in JSON response):

1) reasoning — 1-3 sentences.
   State how the claim is phrased and how consequential its outcome is.

2) prediction_strength — HOW the claim is phrased (NOT how important):

   "high"   — RARE. Explicit numeric/dated threshold with a single measurable
              criterion (e.g., "X will reach Y by date Z").
   "medium" — probabilistic but substantive, with a clear checkable outcome.
   "low"    — vague hedge ("може", "можливо", "скоріше за все", "практично"),
              possibility statement, open-ended trend, or non-substantive
              forecast. MOST political commentary is low.

3) prediction_value — HOW MUCH the predicted outcome matters. Even in
   consequential topics (war, geopolitics), distinguish:

   "high"   — outcome reshapes a country, region, or balance of power.
              Examples: "війна закінчиться у 2026", "Україна стане
              федеральним округом", "Захід вступить у війну з РФ".
              NOT high: process announcements, logistical events,
              announcements of intent within an ongoing conflict.
   "medium" — affects a sector, region, institution, or specific subgroup;
              significant policy/military escalation but not regime-changing.
              Examples: "новий уряд буде сформований", "будуть нові санкції",
              "поставки зброї будуть розширені".
   "low"    — process/logistical/descriptive within a larger context;
              tautology; calendar-bound certainty; announcement of intent
              (not outcome); description of ongoing activity; vague slogan.
              Examples: "дипломати зустрінуться", "позиції політиків
              змінюватимуться залежно від подій", "сторони нарабатывают
              соглашения", "45 евакуаційних автобусів поїдуть з міста".

Respond ONLY with raw JSON, no markdown fences:

{{
  "reasoning": "1-3 sentences",
  "prediction_strength": "low" | "medium" | "high",
  "prediction_value": "low" | "medium" | "high"
}}"""


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


def build_verification_prompt_v2(
    claim: str,
    prediction_date: str,
    target_date: str | None,
    today: str,
    situation: str,
) -> str:
    return VERIFICATION_TEMPLATE_V2.format(
        claim=claim,
        prediction_date=prediction_date,
        target_date=target_date or "not specified",
        today=today,
        situation=situation,
    )


def get_verification_system_v2(today: str) -> str:
    return VERIFICATION_SYSTEM_V2.format(today=today)


def get_assessment_system_v2(today: str) -> str:
    return ASSESSMENT_SYSTEM_V2.format(today=today)


def validate_situation(situation: str | None) -> bool:
    return bool(situation and situation.strip())


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


def parse_verification_response_v2(response: str) -> dict:
    data = json.loads(_strip_code_fence(response))

    required = {"status", "confidence", "prediction_strength", "prediction_value", "reasoning"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"missing required field: {sorted(missing)[0]}")

    if data["status"] not in {"confirmed", "refuted", "unresolved", "premature"}:
        raise ValueError(
            f"invalid status: {data['status']!r} "
            f"(expected confirmed/refuted/unresolved/premature)"
        )

    if data["prediction_strength"] not in {"low", "medium", "high"}:
        raise ValueError(
            f"invalid prediction_strength: {data['prediction_strength']!r} "
            f"(expected low/medium/high)"
        )

    if data["prediction_value"] not in {"low", "medium", "high"}:
        raise ValueError(
            f"invalid prediction_value: {data['prediction_value']!r} "
            f"(expected low/medium/high)"
        )

    status = data["status"]
    retry_after = data.get("retry_after")
    max_horizon = data.get("max_horizon")
    evidence = data.get("evidence") or None

    if status == "premature" and retry_after is None:
        raise ValueError("status=premature requires retry_after")

    if status in {"confirmed", "refuted"} and not evidence:
        raise ValueError(f"status={status} requires evidence")

    if status != "premature" and retry_after is not None:
        logger.warning(
            "soft-normalize: dropping extraneous retry_after on status=%s", status
        )
        data["retry_after"] = None

    if status != "premature" and max_horizon is not None:
        logger.warning(
            "soft-normalize: dropping extraneous max_horizon on status=%s", status
        )
        data["max_horizon"] = None

    data["evidence"] = evidence
    return data


def parse_assessment_response_v2(response: str) -> dict:
    data = json.loads(_strip_code_fence(response))

    if "prediction_strength" not in data:
        raise ValueError("missing required field: prediction_strength")

    if data["prediction_strength"] not in {"low", "medium", "high"}:
        raise ValueError(
            f"invalid prediction_strength: {data['prediction_strength']!r} "
            f"(expected low/medium/high)"
        )

    return {"prediction_strength": data["prediction_strength"]}
