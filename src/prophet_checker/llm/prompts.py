from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import TYPE_CHECKING

from prophet_checker.models.domain import QueryPlan, SearchFilters

if TYPE_CHECKING:
    from prophet_checker.models.domain import Person, RetrievedPrediction

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
   - "Ухвалений закон передбачає, що розголошення даних розвідки каратиметься..." —
     restating provisions of an adopted law/decision is a KNOWN FACT, not a forecast


H. Analysis of intentions, goals, or plans of third parties — NOT the author's forecast:
   - "Кремль хоче: звинуватити Україну у зриві перемир'я, зняти санкції..." —
     enumeration of someone's GOALS; the author does not assert these goals WILL be achieved
   - "ОПЗЖ спробують усунути Зеленського" — characterization of intent, not a forecast of outcome
   Extract ONLY if the author explicitly asserts the third party WILL SUCCEED.

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

R4. MODALITY PRESERVATION — the most common extraction error. Reconstruction
    (R1-R3) must NEVER upgrade modality:
    - Obligation/demand/proposal is NOT a forecast: "повинні бути звільнені",
      "треба", "необхідно", "пропоную", "закликаю", "надо подумать",
      "должны быть" → do NOT extract, do NOT rewrite as "буде/станеться".
    - Hedged claims must keep their hedge: "сприятиме X" ≠ "призведе до X";
      "має шанси" ≠ "станеться". If the claim only passes criterion 2 after
      strengthening the hedge — do NOT extract it.
    - Questions, interview timecodes, quoted theses the author criticizes —
      are NOT assertions by the author.
    - Sarcastic warnings and rhetorical dilemmas are NOT forecasts:
      "Мало вам не покажется" is a threat-flavored quip, not a prediction;
      "його доля незавидна: припинити війну і втратити посаду, або програти"
      describes a dilemma, not a forecast of which outcome will occur.

EXAMPLE (enumerated agenda the author predicts will fail):
Source: "Ожидаемые вехи на пути комиссии Ермак-Козак: — прекращение огня;
— вода в Крым; — выборы в ОРДЛО... Поэтому, я думаю что у Путина-Зеленского
не получится."
WRONG -> ["прекращение огня;", "вода в Крым;", "выборы в ОРДЛО;"]
        (fragments; inverted polarity — author predicts these will NOT happen)
RIGHT -> "Спроба поетапного примирення з РФ через комісію Єрмак–Козак
        (припинення вогню, вода в Крим, вибори в ОРДЛО) зрештою провалиться."

EXAMPLE (demand, not forecast):
Source: "Все оккупированные районы Нагорного Карабаха должны быть освобождены."
WRONG -> "Азербайджан повністю звільнить усі окуповані райони..."
         (the author demands it; he does not forecast it)
RIGHT -> (nothing extracted)

EXAMPLE (interview timecode phrased as a question):
Source: "0:31 Україна вистоїть, як держава? 1:05 Чи буде наступ на Київ?"
WRONG -> "Україна вистоїть як держава у разі широкомасштабної війни."
         (fabricated an assertion from a question heading)
RIGHT -> (nothing extracted)

EXAMPLE (abstract societal "law" without verifiable criterion):
Source: "Эволюционный закон: власть в Украине всегда будет получать сила,
представляющая поликультурное, городское, социально активное население."
WRONG -> "В Україні політичну владу завжди здобуватиме та політична сила, яка
         представлятиме інтереси полікультурного, міського населення."
         (an abstract "law" — no specific election, date, or measurable threshold)
RIGHT -> (nothing extracted)

EXAMPLE (rhetorical doom without criteria):
Source: "Социальная система Украины будет разрушена полностью. Без возможности возврата."
WRONG -> "Поточна соціальна система України буде повністю зруйнована без можливості повернення."
         (no checkable event, no timeframe — vague forward rhetoric, category D)
RIGHT -> (nothing extracted)


FINAL GATE — apply to every candidate claim BEFORE emitting it:
"Does the AUTHOR assert this WILL happen?" If the source phrasing is a demand,
proposal, hope, question, or an enumeration of someone's goals — DROP the claim.
When in doubt, DROP: a missed prediction is cheaper than a fabricated one.

SECOND GATE — verifiability: for each claim that passed the first gate, name to
yourself the concrete event, threshold, or date a fact-checker could check.
A thesis about societal trends, national character, or "historical laws" has no
such criterion — DROP it. If you cannot say WHAT exactly will be checkable, DROP.

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


RAG_SYSTEM = """You are Prophet Checker, an assistant that answers questions about predictions
made by Ukrainian public figures, based ONLY on the prediction data provided in the user message.

Write a SHORT, natural answer in Ukrainian — a few sentences, suitable for a chat message.

For each relevant prediction:
1. Say what was predicted: the claim in plain language, with its reasoning/context and timing
   (when it was made and the horizon), phrased naturally — e.g. "у серпні 2020 року прогнозували… до 2035 року".
2. State the verdict explicitly, translating the status into plain Ukrainian:
   - confirmed  → "прогноз справдився"
   - refuted    → "прогноз не справдився"
   - unresolved → "однозначно оцінити не вдалося"
   - premature  → "ще зарано судити — термін прогнозу ще не настав"

If several predictions are relevant, weave them into one coherent answer, each with its own verdict.

Cite your sources: put the prediction's identifier in square brackets immediately after the
statement it supports, e.g. "…розпадеться до 2024 року [7c9f4e21-3a8b-4d15-9e02-6b1f8a4c7d33]".
Use ONLY identifiers that appear in the source block. Every prediction you discuss gets its
identifier. An identifier may appear ONLY inside square brackets, never in running prose.

Do NOT put in the answer: the confidence number, the raw English status label
(confirmed/refuted/unresolved/premature), invented statistics (e.g. "0% успішності"), or
meta-statements about the database. Use the provided dates and status only to inform the wording —
never recite them as labelled fields.

Finish with exactly one short line: "Аналіз автоматизований і може містити неточності."
Respond in Ukrainian."""

RAG_TEMPLATE = """Question: {question}

Relevant predictions:
---
{predictions_context}
---

Answer the user's question following the rules in the system prompt: lead with what was predicted
(plain language, with context and timing), then state the verdict in plain Ukrainian. Weave multiple
predictions into one coherent answer. Keep it short. Cite each prediction you discuss by putting
its identifier in square brackets right after the statement — inside brackets only, never in
running prose. No confidence numbers, no raw status labels, no invented statistics. End with the
single disclaimer line."""


SELF_QUERY_SYSTEM = """You are a query planner for a database of predictions made by Ukrainian public figures.

Convert the user question (Ukrainian/Russian/English) into a JSON retrieval plan with a
semantic query and structured filters. You do NOT answer the question.

Filterable fields:
- person_id (string): prediction author. Match author mentions against the provided list
  of known persons (name variants and transliterations count as a match).
- prediction_date (date): when the prediction was MADE ("що казав у 2022" → this field).
- target_date (date): the time the prediction is ABOUT ("прогнози на 2023" → this field).

Rules:
1. semantic_query: the question stripped of author names and date constraints — keep only
   the topic. If nothing remains, restate the topic of the question in a few words.
2. Author mentioned and found in the list → person_id = its id, unknown_author = null.
3. Author mentioned but NOT in the list → unknown_author = the name exactly as mentioned
   in the question, person_id = null.
4. No author mentioned → person_id = null and unknown_author = null.
5. "When it was said" constraints → prediction_date_from/to. "About what time" constraints
   → target_date_from/to. A bare year YYYY expands to YYYY-01-01 .. YYYY-12-31.
6. Relative expressions ("минулого року", "нещодавно", "last month") resolve against
   today's date from the prompt.
7. Dates are ISO YYYY-MM-DD or null. Never invent constraints absent from the question.

Examples (assume known person "Олексій Арестович" id=a1, today 2026-07-11):
Q: "Що Арестович казав про Крим у 2022?"
{"semantic_query": "прогнози про Крим", "person_id": "a1", "unknown_author": null,
 "prediction_date_from": "2022-01-01", "prediction_date_to": "2022-12-31",
 "target_date_from": null, "target_date_to": null}
Q: "Які були прогнози на 2024 рік щодо завершення війни?"
{"semantic_query": "завершення війни", "person_id": null, "unknown_author": null,
 "prediction_date_from": null, "prediction_date_to": null,
 "target_date_from": "2024-01-01", "target_date_to": "2024-12-31"}
Q: "Що прогнозував Портников про вибори?"
{"semantic_query": "прогнози про вибори", "person_id": null, "unknown_author": "Портников",
 "prediction_date_from": null, "prediction_date_to": null,
 "target_date_from": null, "target_date_to": null}

Respond with ONLY the JSON object — no markdown fence, no commentary."""

SELF_QUERY_TEMPLATE = """Today: {today}

Known persons:
{persons}

Question: {question}"""


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
        text=text,
        person_name=person_name,
        published_date=published_date,
    )


def render_predictions(sources: list[RetrievedPrediction]) -> str:
    """Render retrieved predictions as the source block. Single source of truth so the
    generator (build_rag_prompt) and the faithfulness judge grade against the SAME view."""
    lines = []
    for s in sources:
        p = s.prediction
        target = f", target: {p.target_date.isoformat()}" if p.target_date else ""
        situation = f" | situation: {p.situation}" if p.situation else ""
        lines.append(
            f"[{p.id}] {p.claim_text}{situation} "
            f"(date: {p.prediction_date.isoformat()}{target}, "
            f"status: {p.status.value}, confidence: {p.confidence})"
        )
    return "\n".join(lines)


def build_rag_prompt(question: str, sources: list[RetrievedPrediction]) -> str:
    return RAG_TEMPLATE.format(question=question, predictions_context=render_predictions(sources))


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
            f"invalid status: {data['status']!r} (expected confirmed/refuted/unresolved/premature)"
        )

    if data["prediction_strength"] not in {"low", "medium", "high"}:
        raise ValueError(
            f"invalid prediction_strength: {data['prediction_strength']!r} "
            f"(expected low/medium/high)"
        )

    if data["prediction_value"] not in {"low", "medium", "high"}:
        raise ValueError(
            f"invalid prediction_value: {data['prediction_value']!r} (expected low/medium/high)"
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
        logger.warning("soft-normalize: dropping extraneous retry_after on status=%s", status)
        data["retry_after"] = None

    if status != "premature" and max_horizon is not None:
        logger.warning("soft-normalize: dropping extraneous max_horizon on status=%s", status)
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


def build_self_query_prompt(question: str, persons: list[Person], today: date) -> str:
    lines = [f"- {p.name} (id: {p.id})" for p in persons]
    return SELF_QUERY_TEMPLATE.format(
        today=today.isoformat(), persons="\n".join(lines), question=question
    )


_QUERY_PLAN_DATE_FIELDS = (
    "prediction_date_from",
    "prediction_date_to",
    "target_date_from",
    "target_date_to",
)


def _parse_plan_dates(data: dict) -> dict[str, date | None]:
    dates: dict[str, date | None] = {}
    for field in _QUERY_PLAN_DATE_FIELDS:
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"date field {field} must be an ISO string, got {type(value).__name__}"
            )
        dates[field] = date.fromisoformat(value) if value is not None else None

    for prefix in ("prediction_date", "target_date"):
        lo, hi = dates[f"{prefix}_from"], dates[f"{prefix}_to"]
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"inverted {prefix} range: {lo} > {hi}")

    return dates


def parse_query_plan(raw: str, known_person_ids: set[str], question: str) -> QueryPlan:
    data = json.loads(_strip_code_fence(raw))
    if not isinstance(data, dict):
        raise ValueError(f"planner returned non-object JSON: {type(data).__name__}")

    person_id = data.get("person_id")
    unknown_author = (data.get("unknown_author") or "").strip() or None
    if person_id is not None and person_id not in known_person_ids:
        raise ValueError(f"unknown person_id from planner: {person_id!r}")
    if person_id is not None and unknown_author is not None:
        raise ValueError("person_id and unknown_author are mutually exclusive")

    dates = _parse_plan_dates(data)
    semantic_query = (data.get("semantic_query") or "").strip() or question
    filters = SearchFilters(person_id=person_id, unknown_author=unknown_author, **dates)
    return QueryPlan(semantic_query=semantic_query, filters=filters)
