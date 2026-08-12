# Extraction Eval v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the v2 extraction eval as a new consumer of `eval_common`, so a run produces an ordered list of extraction-model candidates where every position is explained by a named metric in a named role.

**Architecture:** A new `scripts/extraction/eval_v2/` subpackage plus one upstream dataset builder. The run is multi-stage and composes `eval_common` role-functions directly instead of calling `run_eval()`: participants run first over the whole dataset, then pooling, judging, and reference-set construction happen once per post, then verdicts are projected back onto models. Intermediate artifacts are written to disk after stage 2 and stage 3 so judging can be re-run without paying for extraction or clustering again.

**Tech Stack:** Python 3.14, Pydantic v2, pytest (`asyncio_mode = "auto"`), LiteLLM via `prophet_checker.llm.LLMClient`, `eval_common` (`run_cases`, `EvalCase`, `EvalRun`, `ScoreCard`, `LLMJudge`, `FakeJudge`, `write_report`).

**Design doc:** [`2026-07-25-extraction-eval-v2-design.md`](./2026-07-25-extraction-eval-v2-design.md). This plan implements it and does not re-argue it. Where a step needs a reason, it links to a design section rather than restating it.

## Global Constraints

- Python 3.14, all commands via `.venv/bin/...`. Ruff line length 100. `complexipy` threshold 12, ratchet mode.
- Tests are async-first with no marker (`asyncio_mode = "auto"`). No test may touch the network, Postgres, or the Telegram API.
- Participants run the **production** `PredictionExtractor` with the **production** `EXTRACTION_SYSTEM`. No eval-specific extraction prompt. (Design § "Прогін учасників".)
- Data files under `scripts/data/` carry an ISO creation-date suffix; consumers reference the explicit dated path.
- Never `print()` in `src/`; `print()` is allowed in `scripts/`. Log lazily (`logger.info("x %d", n)`), never per-item at INFO, never post text or judge responses.
- Green and red zone changes never share a commit. Red zone in this plan: Task 1 and Task 2 only (both touch `src/`).
- Participants (P1, closed here):
  - `gemini/gemini-3.1-flash-lite-preview` — production baseline, mandatory
  - `gemini/gemini-3.1-flash-lite`
  - `deepseek/deepseek-v4-flash`
  - `openai/gpt-5.4-nano`
- Judge (P1, closed here): `anthropic/claude-opus-5`. Thinking **disabled** for the per-claim checks, **adaptive** for the clustering and missed-prediction calls. (Rationale: the per-claim checks are verifiable lookups; the two per-post calls are judgment. Volume is ~3 × claims for the former and 1 per post for the latter.)
- Numeric thresholds stay parked until the first run produces them: noise band (P2) and the `precision` gate value (P3). Both live in one constants block, marked `# from first run`.

---

## Task 1: `LLMClient` accepts `claude-opus-5+`

**Scope:** The judge model rejects `temperature`, but `LLMClient` only strips it for three older prefixes, so every judge call would 400. One-line prod fix plus its test. **Red zone — own commit.**

**Files:**
- Modify: `src/prophet_checker/llm/client.py:11`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `LLMClient(provider="anthropic", model="claude-opus-5", ...)` sends no `temperature`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_client.py
from prophet_checker.llm.client import LLMClient


def test_opus_5_drops_temperature():
    client = LLMClient(provider="anthropic", model="claude-opus-5", api_key="x", temperature=0)
    assert client._temperature is None


def test_gemini_keeps_temperature():
    client = LLMClient(provider="gemini", model="gemini-3.1-flash-lite", api_key="x", temperature=0)
    assert client._temperature == 0
```

- [ ] **Step 2: Run the test and confirm the first one fails**

Run: `.venv/bin/python -m pytest tests/test_llm_client.py -v`
Expected: `test_opus_5_drops_temperature` FAILS (`_temperature` is `0`, not `None`).

- [ ] **Step 3: Add the prefix**

```python
# src/prophet_checker/llm/client.py
_NO_TEMPERATURE_MODEL_PREFIXES = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-fable",
)
```

- [ ] **Step 4: Run the tests and confirm both pass**

Run: `.venv/bin/python -m pytest tests/test_llm_client.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/prophet_checker/llm/client.py tests/test_llm_client.py && git commit -m "fix(llm): не слати temperature для claude-opus-5"
```

---

## Task 2: `LLMClient` accumulates token usage + 

**Scope:** `cost_per_post` needs real token counts, and `complete()` currently throws the provider's `usage` away. Add two counters the eval reads after a run. This closes P4 in favour of "from `LLMClient`". **Red zone — own commit.**

**Files:**
- Modify: `src/prophet_checker/llm/client.py`
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Produces: `client.prompt_tokens`, `client.completion_tokens` (ints, monotonically accumulating), `client.reset_usage()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_client.py — append
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


async def test_complete_accumulates_usage():
    client = LLMClient(provider="gemini", model="m", api_key="x")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )
    with patch("prophet_checker.llm.client.acompletion", new=AsyncMock(return_value=response)):
        await client.complete("hi")
        await client.complete("hi")

    assert client.prompt_tokens == 22
    assert client.completion_tokens == 14
    client.reset_usage()
    assert client.prompt_tokens == 0
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_llm_client.py::test_complete_accumulates_usage -v`
Expected: FAIL with `AttributeError: 'LLMClient' object has no attribute 'prompt_tokens'`.

- [ ] **Step 3: Implement**

In `__init__`, after the existing assignments:

```python
        self.prompt_tokens = 0
        self.completion_tokens = 0
```

In `complete()`, replace the final `return` with:

```python
        self._record_usage(getattr(response, "usage", None))
        return response.choices[0].message.content

    def _record_usage(self, usage) -> None:
        # Провайдер може не повернути usage — тоді метрика ціни просто недорахує цей виклик
        if usage is None:
            return
        self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0

    def reset_usage(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
```

- [ ] **Step 4: Run the full client test file**

Run: `.venv/bin/python -m pytest tests/test_llm_client.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/prophet_checker/llm/client.py tests/test_llm_client.py && git commit -m "feat(llm): накопичувати usage-токени в LLMClient"
```

---

## Task 3: Eval data models + 

**Scope:** Every typed boundary the later tasks share — dataset record, extractor output, judge verdicts, per-post scores, metrics. Pure Pydantic declarations, so no unit tests of their own (repo rule: don't unit-test field-only models).

**Files:**
- Create: `scripts/extraction/eval_v2/__init__.py` (empty)
- Create: `scripts/extraction/eval_v2/eval_models.py`

**Interfaces:**
- Produces: `PostInput`, `ExtractionResult`, `PooledClaim`, `ClaimVerdict`, `MissedClaim`, `PostJudgement`, `ModelPostScore`, `PostScore`, `GateSpec`, `MetricSpec`, `ModelMetrics`, `SliceMetrics`, `ExtractionMetrics`.

- [ ] **Step 1: Write the models**

```python
# scripts/extraction/eval_v2/eval_models.py
from __future__ import annotations

from pydantic import BaseModel

from prophet_checker.models.domain import Prediction


class PostInput(BaseModel):
    """EvalCase.input — один пост датасету."""

    post_id: str
    author: str
    channel: str
    text: str
    published_date: str
    stratum: str  # "prefilter" | "random"
    url: str = ""


class ExtractionResult(BaseModel):
    """EvalRun.result — те, що прод-екстрактор витяг із поста."""

    predictions: list[Prediction]


class PooledClaim(BaseModel):
    """Унікальний claim поста після дедуплікації (крок 3)."""

    claim_id: str
    claim_text: str
    situation: str | None = None
    models: list[str]  # учасники, чий claim потрапив у цей кластер


class ClaimVerdict(BaseModel):
    """Три перевірні питання по одному унікальному claim (крок 4)."""

    claim_id: str
    claim_grounded: bool
    situation_grounded: bool
    passes_rubric: bool
    truncated: bool = False
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        return (
            self.claim_grounded
            and self.situation_grounded
            and self.passes_rubric
            and not self.truncated
        )

    @property
    def is_hallucinated(self) -> bool:
        return not self.claim_grounded or not self.situation_grounded

    @property
    def is_over_extraction(self) -> bool:
        return self.claim_grounded and self.situation_grounded and not self.passes_rubric


class MissedClaim(BaseModel):
    text: str
    reason: str = ""


class PostJudgement(BaseModel):
    """Усе, що суддя сказав про один пост — іде в ScoreCard.detail."""

    post_id: str
    claims: list[PooledClaim]
    verdicts: list[ClaimVerdict]
    missed: list[MissedClaim]
    judge_errors: int = 0


class ModelPostScore(BaseModel):
    extracted: int
    valid: int
    hallucinated: int
    over_extracted: int
    covered: int


class PostScore(BaseModel):
    post_id: str
    author: str
    stratum: str
    reference_size: int
    per_model: dict[str, ModelPostScore]


class GateSpec(BaseModel):
    threshold: float
    blocking: bool
    relative_to_baseline: bool = False


class MetricSpec(BaseModel):
    """Дві незалежні здатності метрики. Обидві None = diagnostic."""

    name: str
    gate: GateSpec | None = None
    criterion_priority: int | None = None


class SliceMetrics(BaseModel):
    n_posts: int
    coverage: float | None
    precision: float | None


class ModelMetrics(BaseModel):
    model_id: str
    n_posts: int
    hallucination_rate: float | None
    precision: float | None
    coverage: float | None
    over_extraction_rate: float | None
    determinism: float | None = None
    cost_per_post: float | None = None
    by_author: dict[str, SliceMetrics] = {}
    by_stratum: dict[str, SliceMetrics] = {}


class ExtractionMetrics(BaseModel):
    """Metrics-сабтайп консумера — те, що лягає в EvalReport.metrics."""

    baseline_model: str
    per_model: dict[str, ModelMetrics]
    winner: str | None = None
    decided_by: str | None = None
    flags: list[str] = []
```

- [ ] **Step 2: Confirm the package imports**

Run: `.venv/bin/python -c "from extraction.eval_v2.eval_models import ExtractionMetrics; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add scripts/extraction/eval_v2/ && git commit -m "feat(eval-v2): типізовані моделі консумера"
```

---

## Task 4: Dataset loader + 

**Scope:** Turn the dataset JSON into `EvalCase[]`, failing loudly on a malformed record before any LLM call is made. (Design § "Форма артефакту".)

**Files:**
- Create: `scripts/extraction/eval_v2/dataset.py`
- Test: `tests/test_eval_v2_dataset.py`

**Interfaces:**
- Consumes: `PostInput` (Task 3).
- Produces: `load_eval_dataset(path: Path) -> list[EvalCase]`, `DATASET_PATH` constant.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_v2_dataset.py
import json

import pytest

from extraction.eval_v2.dataset import load_eval_dataset

_POST = {
    "id": "O_Arestovich_official_9001",
    "author": "Арестович",
    "channel": "@O_Arestovich_official",
    "text": "Ціни зростуть.",
    "published_date": "2026-06-01",
    "stratum": "prefilter",
    "url": "https://t.me/x/1",
}


def _write(tmp_path, posts):
    path = tmp_path / "ds.json"
    path.write_text(json.dumps({"metadata": {}, "posts": posts}), encoding="utf-8")
    return path


def test_loads_posts_into_cases(tmp_path):
    cases = load_eval_dataset(_write(tmp_path, [_POST]))
    assert len(cases) == 1
    assert cases[0].id == "O_Arestovich_official_9001"
    assert cases[0].input.stratum == "prefilter"
    assert cases[0].labels is None  # reference-free за побудовою


def test_malformed_record_fails_loud(tmp_path):
    broken = {**_POST}
    del broken["published_date"]
    with pytest.raises(ValueError, match="O_Arestovich_official_9001"):
        load_eval_dataset(_write(tmp_path, [broken]))


def test_unknown_stratum_fails_loud(tmp_path):
    with pytest.raises(ValueError, match="stratum"):
        load_eval_dataset(_write(tmp_path, [{**_POST, "stratum": "handpicked"}]))
```

- [ ] **Step 2: Run and confirm all three fail**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_dataset.py -v`
Expected: 3 errors — `ModuleNotFoundError: No module named 'extraction.eval_v2.dataset'`.

- [ ] **Step 3: Implement**

```python
# scripts/extraction/eval_v2/dataset.py
from __future__ import annotations

import json
from pathlib import Path

from eval_common.models import EvalCase
from extraction.eval_v2.eval_models import PostInput
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
DATASET_PATH = PROJECT_ROOT / "scripts" / "data" / "extraction" / "eval_dataset_2026-08-08.json"

STRATA = ("prefilter", "random")


def load_eval_dataset(path: Path) -> list[EvalCase]:
    """JSON → EvalCase[]. Падає на першому кривому записі, до будь-якого LLM-виклику."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    for raw in payload["posts"]:
        cases.append(_build_case(raw))
    return cases


def _build_case(raw: dict) -> EvalCase:
    post_id = raw.get("id", "<без id>")
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
```

- [ ] **Step 4: Run and confirm all pass**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_dataset.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/eval_v2/dataset.py tests/test_eval_v2_dataset.py && git commit -m "feat(eval-v2): лоадер датасету з fail-loud"
```

---

## Task 5: Judge prompts and parsers

**Scope:** All four judge calls in one module — clustering, the three per-claim checks, and the missed-predictions call — each with its parser. The rubric itself is not restated here: the prompts quote the production `EXTRACTION_SYSTEM`, so the eval measures the model against the live rubric. (Design § "Питання до судді".)

**Files:**
- Create: `scripts/extraction/eval_v2/judge_prompts.py`
- Test: `tests/test_eval_v2_judge_prompts.py`

**Interfaces:**
- Consumes: `PooledClaim`, `ClaimVerdict`, `MissedClaim` (Task 3).
- Produces: `CLUSTER_SYSTEM`, `CHECK_SYSTEM`, `MISSED_SYSTEM`, `build_cluster_prompt`, `build_check_prompt`, `build_missed_prompt`, `parse_cluster_response`, `parse_check_response`, `parse_missed_response`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_v2_judge_prompts.py
import pytest

from extraction.eval_v2.judge_prompts import (
    build_check_prompt,
    build_cluster_prompt,
    parse_check_response,
    parse_cluster_response,
    parse_missed_response,
)


def test_parse_cluster_groups_indices():
    groups = parse_cluster_response('{"clusters": [[0, 2], [1]]}')
    assert groups == [[0, 2], [1]]


def test_parse_cluster_tolerates_fenced_json():
    groups = parse_cluster_response('```json\n{"clusters": [[0]]}\n```')
    assert groups == [[0]]


def test_parse_check_reads_three_answers():
    verdict = parse_check_response(
        '{"claim_grounded": true, "situation_grounded": false, '
        '"passes_rubric": true, "truncated": false, "reason": "парафраз"}',
        claim_id="p1:0",
    )
    assert verdict.claim_id == "p1:0"
    assert verdict.is_valid is False
    assert verdict.is_hallucinated is True


def test_parse_missed_returns_claims():
    missed = parse_missed_response('{"missed": [{"text": "Курс впаде", "reason": "прогноз"}]}')
    assert [m.text for m in missed] == ["Курс впаде"]


def test_parse_check_raises_on_garbage():
    with pytest.raises(ValueError):
        parse_check_response("не json взагалі", claim_id="p1:0")


def test_cluster_prompt_numbers_claims():
    prompt = build_cluster_prompt("текст поста", ["Ціни зростуть", "Ціни не зростуть"])
    assert "0. Ціни зростуть" in prompt
    assert "1. Ціни не зростуть" in prompt


def test_check_prompt_carries_post_and_claim():
    prompt = build_check_prompt("текст поста", "Ціни зростуть", "ситуація")
    assert "текст поста" in prompt
    assert "Ціни зростуть" in prompt
```

- [ ] **Step 2: Run and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_judge_prompts.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# scripts/extraction/eval_v2/judge_prompts.py
from __future__ import annotations

import json
import re

from extraction.eval_v2.eval_models import ClaimVerdict, MissedClaim
from prophet_checker.llm.prompts import get_extraction_system

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)

CLUSTER_SYSTEM = (
    "Ти групуєш твердження, витягнуті різними моделями з ОДНОГО поста. "
    "Два твердження в одній групі тільки якщо вони стверджують те саме передбачення. "
    "Заперечення, протилежний напрямок або інший часовий обрій — це РІЗНІ твердження, "
    "навіть якщо формулювання майже збігаються. Відповідаєш ЛИШЕ валідним JSON."
)

CHECK_SYSTEM = (
    "Ти — суворий перевіряльник. Відповідаєш на три перевірні питання про ОДНЕ твердження "
    "проти тексту поста. Кожне питання має однозначну відповідь; не оцінюй, наскільки добре "
    "твердження сформульоване. Відповідаєш ЛИШЕ валідним JSON."
)

MISSED_SYSTEM = (
    "Ти шукаєш передбачення в тексті поста, яких немає у поданому списку. "
    "Додаєш лише те, що проходить рубрику. Відповідаєш ЛИШЕ валідним JSON."
)


def _extract_json(text: str) -> dict:
    """Перший JSON-обʼєкт відповіді; хвіст після нього ігнорується."""
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
        "потрапити рівно в одну групу. Твердження без пари утворює групу з одного елемента. "
        'Формат: {"clusters": [[0, 2], [1]]}\n\n'
        f"ПОСТ:\n{post_text}\n\nТВЕРДЖЕННЯ:\n" + "\n".join(numbered)
    )


def parse_cluster_response(text: str) -> list[list[int]]:
    data = _extract_json(text)
    clusters: list[list[int]] = []
    for group in data.get("clusters", []):
        clusters.append([int(i) for i in group])
    return clusters


def build_check_prompt(post_text: str, claim_text: str, situation: str | None) -> str:
    return (
        "Дай відповідь на чотири питання про ТВЕРДЖЕННЯ проти ПОСТА:\n"
        "1) claim_grounded — чи простежується твердження до тексту поста;\n"
        "2) situation_grounded — чи простежується до тексту поле СИТУАЦІЯ "
        "(це парафраз, дослівного збігу не буде);\n"
        "3) passes_rubric — чи це передбачення за рубрикою нижче;\n"
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
    listed = "\n".join(f"- {c}" for c in claim_texts) or "— (нічого не витягнуто)"
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
```

- [ ] **Step 4: Run and confirm all pass**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_judge_prompts.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/eval_v2/judge_prompts.py tests/test_eval_v2_judge_prompts.py && git commit -m "feat(eval-v2): промпти й парсери судді"
```

---

## Task 6: Pooling

**Scope:** Collapse every participant's claims for one post into unique claims — exact-match merge in code, everything else via one judge clustering call. The threshold is gone by design; code never decides that two different strings are the same claim. (Design § "Чому не поріг схожості".)

**Files:**
- Create: `scripts/extraction/eval_v2/pool.py`
- Test: `tests/test_eval_v2_pool.py`

**Interfaces:**
- Consumes: `PooledClaim` (Task 3), `build_cluster_prompt` / `parse_cluster_response` (Task 5), `Judge` protocol.
- Produces: `normalize(text) -> str`, `pool_claims(post_id, post_text, by_model, judge) -> list[PooledClaim]` where `by_model: dict[str, list[Prediction]]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_v2_pool.py
from datetime import date

from eval_common.fakes import FakeJudge
from extraction.eval_v2.pool import normalize, pool_claims
from prophet_checker.models.domain import Prediction


def _prediction(claim: str, situation: str = "ситуація") -> Prediction:
    return Prediction(
        id=claim,
        document_id="p1",
        person_id="Арестович",
        claim_text=claim,
        situation=situation,
        prediction_date=date(2026, 6, 1),
    )


def test_normalize_strips_case_space_punctuation():
    assert normalize("  Ціни  ЗРОСТУТЬ! ") == normalize("ціни зростуть")


async def test_exact_duplicates_merge_without_judge():
    judge = FakeJudge('{"clusters": [[0]]}')
    by_model = {
        "a": [_prediction("Ціни зростуть")],
        "b": [_prediction("ціни зростуть!")],
    }
    claims = await pool_claims("p1", "текст", by_model, judge)
    assert len(claims) == 1
    assert sorted(claims[0].models) == ["a", "b"]


async def test_judge_clusters_non_identical_claims():
    judge = FakeJudge('{"clusters": [[0, 1]]}')
    by_model = {
        "a": [_prediction("Ціни зростуть")],
        "b": [_prediction("Буде зростання цін")],
    }
    claims = await pool_claims("p1", "текст", by_model, judge)
    assert len(claims) == 1
    assert sorted(claims[0].models) == ["a", "b"]


async def test_judge_keeps_negation_apart():
    judge = FakeJudge('{"clusters": [[0], [1]]}')
    by_model = {
        "a": [_prediction("Ціни зростуть")],
        "b": [_prediction("Ціни не зростуть")],
    }
    claims = await pool_claims("p1", "текст", by_model, judge)
    assert len(claims) == 2


async def test_no_claims_skips_judge_entirely():
    judge = FakeJudge("не викликається")
    claims = await pool_claims("p1", "текст", {"a": [], "b": []}, judge)
    assert claims == []


async def test_claim_missing_from_clusters_survives_as_singleton():
    judge = FakeJudge('{"clusters": [[0]]}')  # суддя загубив індекс 1
    by_model = {"a": [_prediction("Перше")], "b": [_prediction("Друге")]}
    claims = await pool_claims("p1", "текст", by_model, judge)
    assert len(claims) == 2
```

- [ ] **Step 2: Run and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_pool.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# scripts/extraction/eval_v2/pool.py
from __future__ import annotations

import logging
import re

from eval_common.judge import Judge
from extraction.eval_v2.eval_models import PooledClaim
from extraction.eval_v2.judge_prompts import (
    CLUSTER_SYSTEM,
    build_cluster_prompt,
    parse_cluster_response,
)
from prophet_checker.models.domain import Prediction

logger = logging.getLogger(__name__)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Тільки для точної рівності рядків — не міра схожості."""
    lowered = _PUNCT_RE.sub(" ", text.lower())
    return _SPACE_RE.sub(" ", lowered).strip()


def _merge_exact(by_model: dict[str, list[Prediction]]) -> list[dict]:
    """Точні дублікати після нормалізації зливаються в коді, без виклику судді."""
    merged: dict[str, dict] = {}
    for model_id, predictions in by_model.items():
        for prediction in predictions:
            key = normalize(prediction.claim_text)
            if not key:
                continue
            entry = merged.get(key)
            if entry is None:
                merged[key] = {
                    "claim_text": prediction.claim_text,
                    "situation": prediction.situation,
                    "models": [model_id],
                }
                continue
            if model_id not in entry["models"]:
                entry["models"].append(model_id)
    return list(merged.values())


async def _cluster_groups(post_text: str, entries: list[dict], judge: Judge) -> list[list[int]]:
    """Групи індексів від судді; при збої кожен claim лишається окремим."""
    if len(entries) < 2:
        return [[i] for i in range(len(entries))]
    prompt = build_cluster_prompt(post_text, [e["claim_text"] for e in entries])
    try:
        raw = await judge.assess(prompt, system=CLUSTER_SYSTEM)
        return parse_cluster_response(raw)
    except (ValueError, KeyError):
        logger.exception("кластеризація не розібралась — claims лишаються окремими")
        return [[i] for i in range(len(entries))]


def _add_missing_singletons(groups: list[list[int]], total: int) -> list[list[int]]:
    """Індекс, якого суддя не згадав, не має зникнути з пулу."""
    seen = set()
    for group in groups:
        seen.update(group)
    complete = [group for group in groups if group]
    for index in range(total):
        if index not in seen:
            complete.append([index])
    return complete


async def pool_claims(
    post_id: str,
    post_text: str,
    by_model: dict[str, list[Prediction]],
    judge: Judge,
) -> list[PooledClaim]:
    """Claims усіх учасників по одному посту → множина унікальних claims."""
    entries = _merge_exact(by_model)
    if not entries:
        return []

    groups = await _cluster_groups(post_text, entries, judge)
    groups = _add_missing_singletons(groups, len(entries))

    claims: list[PooledClaim] = []
    for position, group in enumerate(groups):
        members = [entries[i] for i in group if 0 <= i < len(entries)]
        if not members:
            continue
        models: list[str] = []
        for member in members:
            for model_id in member["models"]:
                if model_id not in models:
                    models.append(model_id)
        claims.append(
            PooledClaim(
                claim_id=f"{post_id}:{position}",
                claim_text=members[0]["claim_text"],
                situation=members[0]["situation"],
                models=models,
            )
        )
    return claims
```

- [ ] **Step 4: Run and confirm all pass**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_pool.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/eval_v2/pool.py tests/test_eval_v2_pool.py && git commit -m "feat(eval-v2): пулінг claims через кластеризацію суддею"
```

---

## Task 7: Judging a post and building the reference set

**Scope:** For one post: ask the three checks per unique claim, ask once what everyone missed, and assemble the `reference set` that all models are measured against. This is the fix for v1's floating denominator. (Design § "Конструкція суддівства".)

**Files:**
- Create: `scripts/extraction/eval_v2/judging.py`
- Test: `tests/test_eval_v2_judging.py`

**Interfaces:**
- Consumes: `PooledClaim`, `PostJudgement` (Task 3); judge prompts (Task 5).
- Produces: `judge_post(post_id, post_text, claims, judge) -> PostJudgement`, `reference_size(judgement) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_v2_judging.py
from extraction.eval_v2.eval_models import PooledClaim
from extraction.eval_v2.judging import judge_post, reference_size


class ScriptedJudge:
    """Віддає заготовлені відповіді по черзі — фейк каркаса не вміє різні відповіді."""

    id = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    async def assess(self, prompt: str, *, system: str) -> str:
        return self._responses.pop(0)


_VALID = (
    '{"claim_grounded": true, "situation_grounded": true, '
    '"passes_rubric": true, "truncated": false}'
)
_NOT_RUBRIC = (
    '{"claim_grounded": true, "situation_grounded": true, '
    '"passes_rubric": false, "truncated": false}'
)


def _claim(index: int) -> PooledClaim:
    return PooledClaim(
        claim_id=f"p1:{index}", claim_text=f"claim {index}", situation="s", models=["a"]
    )


async def test_reference_set_is_valid_claims_plus_missed():
    judge = ScriptedJudge([_VALID, _NOT_RUBRIC, '{"missed": [{"text": "ще одне"}]}'])
    judgement = await judge_post("p1", "текст", [_claim(0), _claim(1)], judge)
    assert reference_size(judgement) == 2  # один валідний + один missed


async def test_empty_claim_list_still_asks_about_missed():
    judge = ScriptedJudge(['{"missed": []}'])
    judgement = await judge_post("p1", "текст", [], judge)
    assert judgement.verdicts == []
    assert reference_size(judgement) == 0


async def test_unparsable_check_becomes_sentinel_and_counts():
    judge = ScriptedJudge(["суддя щось намолов", '{"missed": []}'])
    judgement = await judge_post("p1", "текст", [_claim(0)], judge)
    assert judgement.judge_errors == 1
    assert judgement.verdicts[0].is_valid is False
    assert reference_size(judgement) == 0


async def test_unparsable_missed_call_does_not_kill_the_post():
    judge = ScriptedJudge([_VALID, "знову не json"])
    judgement = await judge_post("p1", "текст", [_claim(0)], judge)
    assert judgement.missed == []
    assert judgement.judge_errors == 1
    assert reference_size(judgement) == 1
```

- [ ] **Step 2: Run and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_judging.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# scripts/extraction/eval_v2/judging.py
from __future__ import annotations

import logging

from eval_common.judge import Judge
from extraction.eval_v2.eval_models import ClaimVerdict, PooledClaim, PostJudgement
from extraction.eval_v2.judge_prompts import (
    CHECK_SYSTEM,
    MISSED_SYSTEM,
    build_check_prompt,
    build_missed_prompt,
    parse_check_response,
    parse_missed_response,
)

logger = logging.getLogger(__name__)


def _sentinel(claim_id: str) -> ClaimVerdict:
    """Вичерпані ретраї → claim не зараховується нікому, але пост живе далі."""
    return ClaimVerdict(
        claim_id=claim_id,
        claim_grounded=False,
        situation_grounded=False,
        passes_rubric=False,
        reason="judge-unparsable",
    )


async def _check_claim(
    post_text: str, claim: PooledClaim, judge: Judge
) -> tuple[ClaimVerdict, int]:
    prompt = build_check_prompt(post_text, claim.claim_text, claim.situation)
    try:
        raw = await judge.assess(prompt, system=CHECK_SYSTEM)
        return parse_check_response(raw, claim.claim_id), 0
    except (ValueError, KeyError):
        logger.exception("перевірний вердикт не розібрався: claim=%s", claim.claim_id)
        return _sentinel(claim.claim_id), 1


async def _ask_missed(post_text: str, claims: list[PooledClaim], judge: Judge):
    prompt = build_missed_prompt(post_text, [c.claim_text for c in claims])
    try:
        raw = await judge.assess(prompt, system=MISSED_SYSTEM)
        return parse_missed_response(raw), 0
    except (ValueError, KeyError):
        logger.exception("missed-виклик не розібрався")
        return [], 1


async def judge_post(
    post_id: str, post_text: str, claims: list[PooledClaim], judge: Judge
) -> PostJudgement:
    """Три перевірні питання на claim + один виклик на пост про пропущене."""
    verdicts: list[ClaimVerdict] = []
    errors = 0
    for claim in claims:
        verdict, failed = await _check_claim(post_text, claim, judge)
        verdicts.append(verdict)
        errors += failed

    missed, missed_failed = await _ask_missed(post_text, claims, judge)
    return PostJudgement(
        post_id=post_id,
        claims=claims,
        verdicts=verdicts,
        missed=missed,
        judge_errors=errors + missed_failed,
    )


def reference_size(judgement: PostJudgement) -> int:
    """Унікальні валідні claims усіх учасників + пропущене, назване суддею."""
    valid = 0
    for verdict in judgement.verdicts:
        if verdict.is_valid:
            valid += 1
    return valid + len(judgement.missed)
```

- [ ] **Step 4: Run and confirm all pass**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_judging.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/eval_v2/judging.py tests/test_eval_v2_judging.py && git commit -m "feat(eval-v2): суддівство поста й побудова reference set"
```

---

## Task 8: Projecting verdicts back onto models

**Scope:** Turn one post's judgement into a per-model score row. The regression test for v1's illness lives here: the same claim held by two models must give both the same verdict. (Design § "Проєкція вердиктів на моделі".)

**Files:**
- Create: `scripts/extraction/eval_v2/scorers.py`
- Test: `tests/test_eval_v2_scorers.py`

**Interfaces:**
- Consumes: `PostJudgement`, `PostScore`, `ModelPostScore` (Task 3); `reference_size` (Task 7).
- Produces: `score_post(judgement, post_input, model_ids, extracted_counts) -> PostScore`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_v2_scorers.py
from extraction.eval_v2.eval_models import ClaimVerdict, PooledClaim, PostInput, PostJudgement
from extraction.eval_v2.scorers import score_post

_POST = PostInput(
    post_id="p1",
    author="Арестович",
    channel="@c",
    text="текст",
    published_date="2026-06-01",
    stratum="prefilter",
)


def _verdict(claim_id: str, *, grounded=True, rubric=True) -> ClaimVerdict:
    return ClaimVerdict(
        claim_id=claim_id,
        claim_grounded=grounded,
        situation_grounded=grounded,
        passes_rubric=rubric,
    )


def test_shared_claim_gives_both_models_the_same_verdict():
    """Пряма regression-перевірка на хворобу v1."""
    judgement = PostJudgement(
        post_id="p1",
        claims=[PooledClaim(claim_id="p1:0", claim_text="c", models=["a", "b"])],
        verdicts=[_verdict("p1:0")],
        missed=[],
    )
    score = score_post(judgement, _POST, ["a", "b"], {"a": 1, "b": 1})
    assert score.per_model["a"].valid == score.per_model["b"].valid == 1
    assert score.per_model["a"].covered == score.per_model["b"].covered == 1


def test_hallucination_and_over_extraction_split():
    judgement = PostJudgement(
        post_id="p1",
        claims=[
            PooledClaim(claim_id="p1:0", claim_text="c0", models=["a"]),
            PooledClaim(claim_id="p1:1", claim_text="c1", models=["a"]),
        ],
        verdicts=[
            _verdict("p1:0", grounded=False),
            _verdict("p1:1", rubric=False),
        ],
        missed=[],
    )
    score = score_post(judgement, _POST, ["a"], {"a": 2})
    assert score.per_model["a"].hallucinated == 1
    assert score.per_model["a"].over_extracted == 1
    assert score.per_model["a"].valid == 0


def test_reference_set_is_shared_across_models():
    judgement = PostJudgement(
        post_id="p1",
        claims=[PooledClaim(claim_id="p1:0", claim_text="c", models=["a"])],
        verdicts=[_verdict("p1:0")],
        missed=[{"text": "пропущене"}],
    )
    score = score_post(judgement, _POST, ["a", "b"], {"a": 1, "b": 0})
    assert score.reference_size == 2
    assert score.per_model["a"].covered == 1
    assert score.per_model["b"].covered == 0  # мовчання — провал покриття
```

- [ ] **Step 2: Run and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_scorers.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# scripts/extraction/eval_v2/scorers.py
from __future__ import annotations

from extraction.eval_v2.eval_models import (
    ModelPostScore,
    PostInput,
    PostJudgement,
    PostScore,
)
from extraction.eval_v2.judging import reference_size


def _empty() -> dict[str, int]:
    return {"valid": 0, "hallucinated": 0, "over_extracted": 0, "covered": 0}


def score_post(
    judgement: PostJudgement,
    post: PostInput,
    model_ids: list[str],
    extracted_counts: dict[str, int],
) -> PostScore:
    """Вердикт на унікальний claim проєктується на кожну модель, що його дала."""
    tally = {model_id: _empty() for model_id in model_ids}
    by_claim = {claim.claim_id: claim for claim in judgement.claims}

    for verdict in judgement.verdicts:
        claim = by_claim.get(verdict.claim_id)
        if claim is None:
            continue
        for model_id in claim.models:
            row = tally.get(model_id)
            if row is None:
                continue
            if verdict.is_hallucinated:
                row["hallucinated"] += 1
            if verdict.is_over_extraction:
                row["over_extracted"] += 1
            if verdict.is_valid:
                row["valid"] += 1
                row["covered"] += 1

    per_model = {}
    for model_id in model_ids:
        row = tally[model_id]
        per_model[model_id] = ModelPostScore(
            extracted=extracted_counts.get(model_id, 0),
            valid=row["valid"],
            hallucinated=row["hallucinated"],
            over_extracted=row["over_extracted"],
            covered=row["covered"],
        )

    return PostScore(
        post_id=post.post_id,
        author=post.author,
        stratum=post.stratum,
        reference_size=reference_size(judgement),
        per_model=per_model,
    )
```

- [ ] **Step 4: Run and confirm all pass**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_scorers.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/eval_v2/scorers.py tests/test_eval_v2_scorers.py && git commit -m "feat(eval-v2): проєкція вердиктів на моделі"
```

---

## Task 9: Aggregation

**Scope:** Fold per-post scores into per-model metrics — macro over posts, with the two asymmetries the design insists on: silent posts leave `precision` but stay in `coverage`, and an empty denominator yields `None`, never `0.0`. (Design § "Наскрізні правила".)

**Files:**
- Create: `scripts/extraction/eval_v2/metrics.py`
- Test: `tests/test_eval_v2_metrics.py`

**Interfaces:**
- Consumes: `PostScore`, `ModelMetrics`, `SliceMetrics` (Task 3).
- Produces: `aggregate(scores, model_ids) -> dict[str, ModelMetrics]`, `METRIC_SPECS: list[MetricSpec]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_v2_metrics.py
from extraction.eval_v2.eval_models import ModelPostScore, PostScore
from extraction.eval_v2.metrics import METRIC_SPECS, aggregate


def _score(post_id, *, extracted, valid, covered, reference, hallucinated=0, author="Арестович"):
    return PostScore(
        post_id=post_id,
        author=author,
        stratum="prefilter",
        reference_size=reference,
        per_model={
            "a": ModelPostScore(
                extracted=extracted,
                valid=valid,
                hallucinated=hallucinated,
                over_extracted=0,
                covered=covered,
            )
        },
    )


def test_precision_is_macro_over_posts():
    scores = [
        _score("p1", extracted=2, valid=1, covered=1, reference=2),
        _score("p2", extracted=4, valid=4, covered=4, reference=4),
    ]
    metrics = aggregate(scores, ["a"])["a"]
    assert metrics.precision == (0.5 + 1.0) / 2  # macro, не 5/6


def test_silent_post_leaves_precision_but_stays_in_coverage():
    scores = [
        _score("p1", extracted=0, valid=0, covered=0, reference=2),
        _score("p2", extracted=2, valid=2, covered=2, reference=2),
    ]
    metrics = aggregate(scores, ["a"])["a"]
    assert metrics.precision == 1.0  # p1 виключено — ділити нема на що
    assert metrics.coverage == 0.5  # p1 лишився: мовчання = провал покриття


def test_empty_reference_set_gives_none_not_zero():
    metrics = aggregate([_score("p1", extracted=0, valid=0, covered=0, reference=0)], ["a"])["a"]
    assert metrics.coverage is None
    assert metrics.precision is None


def test_slices_by_author_and_stratum():
    scores = [
        _score("p1", extracted=2, valid=2, covered=2, reference=2, author="Арестович"),
        _score("p2", extracted=2, valid=1, covered=1, reference=2, author="Кущ"),
    ]
    metrics = aggregate(scores, ["a"])["a"]
    assert metrics.by_author["Кущ"].n_posts == 1
    assert metrics.by_author["Кущ"].coverage == 0.5
    assert metrics.by_stratum["prefilter"].n_posts == 2


def test_metric_roles_match_the_design():
    roles = {spec.name: spec for spec in METRIC_SPECS}
    assert roles["hallucination_rate"].gate.blocking is True
    assert roles["precision"].gate.relative_to_baseline is True
    assert roles["coverage"].gate is None  # може впорядковувати, не може відсікати
    assert roles["coverage"].criterion_priority == 1
    assert roles["determinism"].gate.blocking is False
    assert roles["over_extraction_rate"].criterion_priority is None
```

- [ ] **Step 2: Run and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_metrics.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# scripts/extraction/eval_v2/metrics.py
from __future__ import annotations

from extraction.eval_v2.eval_models import (
    GateSpec,
    MetricSpec,
    ModelMetrics,
    PostScore,
    SliceMetrics,
)

# Пороги, зняті з першого прогону — див. plan § "Порогові константи"
PRECISION_GATE_MARGIN = 0.0  # from first run (P3): наскільки нижче базлайну ще прийнятно
NOISE_BAND = 0.0  # from first run (P2)
HALLUCINATION_GATE = 0.02

METRIC_SPECS = [
    MetricSpec(
        name="hallucination_rate",
        gate=GateSpec(threshold=HALLUCINATION_GATE, blocking=True),
    ),
    MetricSpec(
        name="precision",
        gate=GateSpec(threshold=PRECISION_GATE_MARGIN, blocking=True, relative_to_baseline=True),
        criterion_priority=2,
    ),
    MetricSpec(name="determinism", gate=GateSpec(threshold=1.0, blocking=False)),
    MetricSpec(name="coverage", criterion_priority=1),
    MetricSpec(name="cost_per_post", criterion_priority=3),
    MetricSpec(name="over_extraction_rate"),  # diagnostic: не відсікає й не впорядковує
]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


class _Bucket:
    """Накопичувач часток по постах — macro-усереднення робиться в кінці."""

    def __init__(self) -> None:
        self.precision: list[float] = []
        self.coverage: list[float] = []
        self.hallucination: list[float] = []
        self.over_extraction: list[float] = []
        self.n_posts = 0

    def add(self, row, reference_size: int) -> None:
        self.n_posts += 1
        if row.extracted:
            self.precision.append(row.valid / row.extracted)
            self.hallucination.append(row.hallucinated / row.extracted)
            self.over_extraction.append(row.over_extracted / row.extracted)
        if reference_size:
            self.coverage.append(row.covered / reference_size)

    def slice_metrics(self) -> SliceMetrics:
        return SliceMetrics(
            n_posts=self.n_posts,
            coverage=_mean(self.coverage),
            precision=_mean(self.precision),
        )


def _slices(buckets: dict[str, _Bucket]) -> dict[str, SliceMetrics]:
    return {key: bucket.slice_metrics() for key, bucket in buckets.items()}


def aggregate(scores: list[PostScore], model_ids: list[str]) -> dict[str, ModelMetrics]:
    """Macro по постах; розріз авторів і strata — спільних середніх не публікуємо."""
    result: dict[str, ModelMetrics] = {}

    for model_id in model_ids:
        overall = _Bucket()
        by_author: dict[str, _Bucket] = {}
        by_stratum: dict[str, _Bucket] = {}

        for score in scores:
            row = score.per_model.get(model_id)
            if row is None:
                continue
            overall.add(row, score.reference_size)
            by_author.setdefault(score.author, _Bucket()).add(row, score.reference_size)
            by_stratum.setdefault(score.stratum, _Bucket()).add(row, score.reference_size)

        result[model_id] = ModelMetrics(
            model_id=model_id,
            n_posts=overall.n_posts,
            hallucination_rate=_mean(overall.hallucination),
            precision=_mean(overall.precision),
            coverage=_mean(overall.coverage),
            over_extraction_rate=_mean(overall.over_extraction),
            by_author=_slices(by_author),
            by_stratum=_slices(by_stratum),
        )

    return result
```

- [ ] **Step 4: Run and confirm all pass**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_metrics.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/eval_v2/metrics.py tests/test_eval_v2_metrics.py && git commit -m "feat(eval-v2): агрегація метрик macro по постах"
```

---

## Task 10: The selection rule

**Scope:** Gates cut, criteria order, and a criterion lower down is consulted only when the one above it left the difference inside the noise band. Runs on synthetic `ModelMetrics`, so it is testable with no LLM at all. (Design § "Правило вибору".)

**Files:**
- Create: `scripts/extraction/eval_v2/selection.py`
- Test: `tests/test_eval_v2_selection.py`

**Interfaces:**
- Consumes: `ModelMetrics`, `ExtractionMetrics` (Task 3); `NOISE_BAND`, `PRECISION_GATE_MARGIN`, `HALLUCINATION_GATE` (Task 9).
- Produces: `select(per_model, baseline_id, noise_band) -> ExtractionMetrics`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_v2_selection.py
from extraction.eval_v2.eval_models import ModelMetrics
from extraction.eval_v2.selection import select


def _metrics(model_id, *, hallucination=0.0, precision=0.5, coverage=0.5, cost=0.001,
             determinism=1.0):
    return ModelMetrics(
        model_id=model_id,
        n_posts=100,
        hallucination_rate=hallucination,
        precision=precision,
        coverage=coverage,
        over_extraction_rate=0.1,
        determinism=determinism,
        cost_per_post=cost,
    )


def test_hallucination_gate_cuts():
    per_model = {
        "base": _metrics("base"),
        "loud": _metrics("loud", hallucination=0.3, coverage=0.9),
    }
    result = select(per_model, "base", noise_band=0.02)
    assert result.winner == "base"


def test_precision_gate_is_relative_to_baseline():
    per_model = {
        "base": _metrics("base", precision=0.51),
        "sloppy": _metrics("sloppy", precision=0.30, coverage=0.99),
    }
    result = select(per_model, "base", noise_band=0.02)
    assert result.winner == "base"


def test_coverage_decides_when_it_separates():
    per_model = {
        "base": _metrics("base", coverage=0.50),
        "better": _metrics("better", coverage=0.70),
    }
    result = select(per_model, "base", noise_band=0.02)
    assert result.winner == "better"
    assert result.decided_by == "coverage"


def test_precision_decides_inside_the_coverage_noise_band():
    per_model = {
        "base": _metrics("base", coverage=0.50, precision=0.50),
        "tie": _metrics("tie", coverage=0.51, precision=0.70),
    }
    result = select(per_model, "base", noise_band=0.05)
    assert result.winner == "tie"
    assert result.decided_by == "precision"


def test_cost_decides_when_coverage_and_precision_are_both_in_the_band():
    per_model = {
        "base": _metrics("base", coverage=0.50, precision=0.50, cost=0.01),
        "cheap": _metrics("cheap", coverage=0.51, precision=0.51, cost=0.001),
    }
    result = select(per_model, "base", noise_band=0.05)
    assert result.winner == "cheap"
    assert result.decided_by == "cost_per_post"


def test_low_determinism_flags_but_does_not_cut():
    per_model = {
        "base": _metrics("base", coverage=0.50),
        "jittery": _metrics("jittery", coverage=0.80, determinism=0.6),
    }
    result = select(per_model, "base", noise_band=0.02)
    assert result.winner == "jittery"
    assert any("determinism" in flag for flag in result.flags)
```

- [ ] **Step 2: Run and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_selection.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# scripts/extraction/eval_v2/selection.py
from __future__ import annotations

from extraction.eval_v2.eval_models import ExtractionMetrics, ModelMetrics
from extraction.eval_v2.metrics import HALLUCINATION_GATE, PRECISION_GATE_MARGIN

# (метрика, чи більше — краще) у порядку пріоритету criterion
_CRITERIA = (("coverage", True), ("precision", True), ("cost_per_post", False))


def _passes_gates(candidate: ModelMetrics, baseline: ModelMetrics) -> bool:
    if candidate.hallucination_rate is None or candidate.hallucination_rate > HALLUCINATION_GATE:
        return False
    if candidate.precision is None or baseline.precision is None:
        return False
    return candidate.precision >= baseline.precision - PRECISION_GATE_MARGIN


def _value(metrics: ModelMetrics, name: str, higher_is_better: bool) -> float:
    raw = getattr(metrics, name)
    if raw is None:
        return float("-inf")
    return raw if higher_is_better else -raw


def _best_by(survivors: list[ModelMetrics], name: str, higher_is_better: bool, band: float):
    """Найкращий за метрикою і чи відірвався він від другого далі за смугу шуму."""
    ranked = sorted(survivors, key=lambda m: _value(m, name, higher_is_better), reverse=True)
    leader = ranked[0]
    if len(ranked) == 1:
        return leader, True
    gap = _value(leader, name, higher_is_better) - _value(ranked[1], name, higher_is_better)
    return leader, gap > band


def _determinism_flags(survivors: list[ModelMetrics], baseline: ModelMetrics) -> list[str]:
    flags: list[str] = []
    for candidate in survivors:
        if candidate.determinism is None or baseline.determinism is None:
            continue
        if candidate.determinism < baseline.determinism:
            flags.append(
                f"determinism {candidate.model_id}={candidate.determinism:.2f} "
                f"нижче базлайну {baseline.determinism:.2f} — потрібне явне людське рішення"
            )
    return flags


def select(
    per_model: dict[str, ModelMetrics], baseline_id: str, noise_band: float
) -> ExtractionMetrics:
    """Gates відсікають; criterion нижчого пріоритету консультується лише в смузі шуму."""
    baseline = per_model[baseline_id]
    survivors = [m for m in per_model.values() if _passes_gates(m, baseline)]
    if not survivors:
        return ExtractionMetrics(
            baseline_model=baseline_id,
            per_model=per_model,
            winner=None,
            decided_by=None,
            flags=["жоден кандидат не пройшов gates"],
        )

    flags = _determinism_flags(survivors, baseline)
    winner = survivors[0]
    decided_by = _CRITERIA[-1][0]
    for name, higher_is_better in _CRITERIA:
        leader, separated = _best_by(survivors, name, higher_is_better, noise_band)
        winner, decided_by = leader, name
        if separated:
            break

    return ExtractionMetrics(
        baseline_model=baseline_id,
        per_model=per_model,
        winner=winner.model_id,
        decided_by=decided_by,
        flags=flags,
    )
```

- [ ] **Step 4: Run and confirm all pass**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_selection.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/eval_v2/selection.py tests/test_eval_v2_selection.py && git commit -m "feat(eval-v2): правило вибору моделі"
```

---

## Task 11: Determinism

**Scope:** Compare two runs of the same model over the same fixed sub-sample by normalised structure, not bytes: claim list, fields stripped of outer whitespace, order significant. (Design § "`determinism`".)

**Files:**
- Create: `scripts/extraction/eval_v2/determinism.py`
- Test: `tests/test_eval_v2_determinism.py`

**Interfaces:**
- Consumes: `ExtractionResult` (Task 3).
- Produces: `structural_signature(result) -> str`, `determinism_score(first, second) -> float | None`, `determinism_subsample(cases, size) -> list[EvalCase]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_eval_v2_determinism.py
from datetime import date

from extraction.eval_v2.determinism import determinism_score, structural_signature
from extraction.eval_v2.eval_models import ExtractionResult
from prophet_checker.models.domain import Prediction


def _result(*claims: str) -> ExtractionResult:
    predictions = []
    for claim in claims:
        predictions.append(
            Prediction(
                id=claim,
                document_id="p1",
                person_id="a",
                claim_text=claim,
                situation="s",
                prediction_date=date(2026, 6, 1),
            )
        )
    return ExtractionResult(predictions=predictions)


def test_whitespace_only_difference_is_still_deterministic():
    assert structural_signature(_result(" Ціни зростуть ")) == structural_signature(
        _result("Ціни зростуть")
    )


def test_order_matters():
    assert structural_signature(_result("a", "b")) != structural_signature(_result("b", "a"))


def test_score_is_share_of_identical_posts():
    first = {"p1": _result("a"), "p2": _result("a"), "p3": _result("a")}
    second = {"p1": _result("a"), "p2": _result("b"), "p3": _result("a")}
    assert determinism_score(first, second) == 2 / 3


def test_score_is_none_when_there_is_nothing_to_compare():
    assert determinism_score({}, {}) is None
```

- [ ] **Step 2: Run and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_determinism.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# scripts/extraction/eval_v2/determinism.py
from __future__ import annotations

import json

from eval_common.models import EvalCase
from extraction.eval_v2.eval_models import ExtractionResult


def structural_signature(result: ExtractionResult) -> str:
    """Нормалізована структура: список claims, поля без крайніх пробілів, порядок важить."""
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
    """Частка постів з ідентичним виходом на двох прогонах."""
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

    Стратифікація (P5) не потрібна — determinism є non-blocking gate за будь-якого складу;
    важлива лише незмінність підвибірки між моделями й між прогонами.
    """
    ordered = sorted(cases, key=lambda case: case.id)
    if len(ordered) <= size:
        return ordered
    step = len(ordered) / size
    picked = []
    for index in range(size):
        picked.append(ordered[int(index * step)])
    return picked
```

- [ ] **Step 4: Run and confirm all pass**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_determinism.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/eval_v2/determinism.py tests/test_eval_v2_determinism.py && git commit -m "feat(eval-v2): determinism на нормалізованій структурі"
```

---

## Task 12: The runner

**Scope:** Compose everything into one CLI: run participants, persist stage artifacts, pool, judge, score, aggregate, select, write the report. This is the module that replaces `run_eval()` — the run is multi-stage, and the framework explicitly allows composing role-functions directly. (Design § "Розкладка й межі консумера".)

**Files:**
- Create: `scripts/extraction/eval_v2/extraction_eval.py`
- Test: `tests/test_eval_v2_runner.py`

**Interfaces:**
- Consumes: everything from Tasks 3–11.
- Produces: `run_participants`, `judge_all_posts`, `build_report`, `main`. Artifacts: `scripts/outputs/extraction_eval_v2/{extractions.json,pooling.json,report.json,report.md}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_v2_runner.py
import json
from datetime import date

from eval_common.models import EvalCase
from extraction.eval_v2.eval_models import ExtractionResult, PostInput
from extraction.eval_v2.extraction_eval import build_report, judge_all_posts
from prophet_checker.models.domain import Prediction


class ScriptedJudge:
    id = "scripted"

    def __init__(self, responses):
        self._responses = list(responses)

    async def assess(self, prompt: str, *, system: str) -> str:
        return self._responses.pop(0) if self._responses else '{"missed": []}'


def _case(post_id: str) -> EvalCase:
    return EvalCase(
        id=post_id,
        input=PostInput(
            post_id=post_id,
            author="Арестович",
            channel="@c",
            text="текст",
            published_date="2026-06-01",
            stratum="prefilter",
        ),
    )


def _result(claim: str) -> ExtractionResult:
    return ExtractionResult(
        predictions=[
            Prediction(
                id=claim,
                document_id="p1",
                person_id="a",
                claim_text=claim,
                situation="s",
                prediction_date=date(2026, 6, 1),
            )
        ]
    )


async def test_end_to_end_on_fakes(tmp_path):
    cases = [_case("p1")]
    by_model = {"base": {"p1": _result("Ціни зростуть")}, "cand": {"p1": _result("Ціни зростуть")}}
    judge = ScriptedJudge(
        [
            '{"clusters": [[0]]}',
            '{"claim_grounded": true, "situation_grounded": true, '
            '"passes_rubric": true, "truncated": false}',
            '{"missed": []}',
        ]
    )

    scores, judgements = await judge_all_posts(cases, by_model, judge)
    report = build_report(
        cases, scores, judgements, ["base", "cand"], baseline_id="base", judge_id="scripted"
    )

    assert report.metrics.per_model["base"].coverage == 1.0
    assert report.metrics.per_model["cand"].coverage == 1.0
    assert report.metrics.winner in {"base", "cand"}
    # detail мусить бути на кожній картці — урок citation-прогону
    assert report.runs[0].cards[0].detail is not None
    assert json.loads(report.model_dump_json())["metrics"]["baseline_model"] == "base"
```

- [ ] **Step 2: Run and confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_runner.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# scripts/extraction/eval_v2/extraction_eval.py
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from eval_common import EvalMetadata, EvalReport, ScoreCard, ScoredRun, write_report  # noqa: E402
from eval_common.clients import build_eval_llm  # noqa: E402
from eval_common.judge import LLMJudge, fingerprint_prompt  # noqa: E402
from eval_common.models import EvalCase, EvalRun  # noqa: E402
from eval_common.runner import run_cases  # noqa: E402
from extraction.eval_v2.dataset import DATASET_PATH, load_eval_dataset  # noqa: E402
from extraction.eval_v2.determinism import (  # noqa: E402
    determinism_score,
    determinism_subsample,
)
from extraction.eval_v2.eval_models import ExtractionResult, PostJudgement  # noqa: E402
from extraction.eval_v2.judge_prompts import (  # noqa: E402
    CHECK_SYSTEM,
    CLUSTER_SYSTEM,
    MISSED_SYSTEM,
)
from extraction.eval_v2.judging import judge_post  # noqa: E402
from extraction.eval_v2.metrics import NOISE_BAND, aggregate  # noqa: E402
from extraction.eval_v2.pool import pool_claims  # noqa: E402
from extraction.eval_v2.scorers import score_post  # noqa: E402
from extraction.eval_v2.selection import select  # noqa: E402
from prophet_checker.analysis.extractor import PredictionExtractor  # noqa: E402

logger = logging.getLogger(__name__)

OUT_DIR = PROJECT_ROOT / "scripts" / "outputs" / "extraction_eval_v2"

BASELINE_MODEL = "gemini/gemini-3.1-flash-lite-preview"
PARTICIPANTS = [
    BASELINE_MODEL,
    "gemini/gemini-3.1-flash-lite",
    "deepseek/deepseek-v4-flash",
    "openai/gpt-5.4-nano",
]
JUDGE_MODEL = "anthropic/claude-opus-5"

# $/1M токенів, звірено з прайс-сторінками провайдерів 2026-08-08
PRICES = {
    "gemini/gemini-3.1-flash-lite-preview": (0.25, 1.50),
    "gemini/gemini-3.1-flash-lite": (0.25, 1.50),
    "deepseek/deepseek-v4-flash": (0.14, 0.28),
    "openai/gpt-5.4-nano": (0.20, 1.25),
}


async def run_participants(
    cases: list[EvalCase], model_id: str, concurrency: int
) -> tuple[dict[str, ExtractionResult], float | None]:
    """Один прохід прод-екстрактора по всьому датасету. Повертає результати й ціну на пост."""
    llm = build_eval_llm(model_id, temperature=0)
    extractor = PredictionExtractor(llm)

    async def run_one(case: EvalCase) -> ExtractionResult:
        post = case.input
        predictions = await extractor.extract(
            text=post.text,
            person_id=post.author,
            document_id=post.post_id,
            person_name=post.author,
            published_date=post.published_date,
        )
        return ExtractionResult(predictions=predictions)

    runs = await run_cases(cases, run_one, concurrency=concurrency)
    results: dict[str, ExtractionResult] = {}
    for run in runs:
        if run.result is not None:
            results[run.case.id] = run.result
    return results, _cost_per_post(llm, model_id, len(cases))


def _cost_per_post(llm, model_id: str, n_posts: int) -> float | None:
    price = PRICES.get(model_id)
    if price is None or not n_posts:
        return None
    prompt_price, completion_price = price
    total = (llm.prompt_tokens * prompt_price + llm.completion_tokens * completion_price) / 1e6
    return total / n_posts


async def judge_all_posts(
    cases: list[EvalCase],
    by_model: dict[str, dict[str, ExtractionResult]],
    judge,
) -> tuple[list, list[PostJudgement]]:
    """Кроки 3–5: пулінг, суддівство, reference set — по одному разу на пост."""
    scores = []
    judgements = []
    model_ids = list(by_model)
    for case in cases:
        post = case.input
        claims_by_model = {}
        extracted_counts = {}
        for model_id in model_ids:
            result = by_model[model_id].get(case.id)
            predictions = result.predictions if result else []
            claims_by_model[model_id] = predictions
            extracted_counts[model_id] = len(predictions)

        claims = await pool_claims(case.id, post.text, claims_by_model, judge)
        judgement = await judge_post(case.id, post.text, claims, judge)
        judgements.append(judgement)
        scores.append(score_post(judgement, post, model_ids, extracted_counts))
    return scores, judgements


def build_report(
    cases: list[EvalCase],
    scores: list,
    judgements: list[PostJudgement],
    model_ids: list[str],
    baseline_id: str,
    judge_id: str,
    determinism: dict[str, float | None] | None = None,
    costs: dict[str, float | None] | None = None,
) -> EvalReport:
    per_model = aggregate(scores, model_ids)
    for model_id, metrics in per_model.items():
        metrics.determinism = (determinism or {}).get(model_id)
        metrics.cost_per_post = (costs or {}).get(model_id)

    metrics = select(per_model, baseline_id, NOISE_BAND)

    scored_runs = []
    for case, judgement, score in zip(cases, judgements, scores, strict=True):
        run = EvalRun(case=case, result=None, latency_s=0.0)
        # detail на кожній картці: без нього перший результат недіагностовний
        card = ScoreCard(scorer="post_judgement", score=None, detail=judgement)
        scored_runs.append(ScoredRun(run=run, cards=[card, ScoreCard(
            scorer="post_score", score=float(score.reference_size), detail=score
        )]))

    metadata = EvalMetadata(
        eval_name="extraction_v2",
        created_at=datetime.now(UTC).isoformat(),
        n_cases=len(cases),
        sut_models={model_id: model_id for model_id in model_ids},
        judge_id=judge_id,
        prompt_fingerprints={
            "cluster": fingerprint_prompt(CLUSTER_SYSTEM),
            "check": fingerprint_prompt(CHECK_SYSTEM),
            "missed": fingerprint_prompt(MISSED_SYSTEM),
        },
        dataset_path=str(DATASET_PATH),
    )
    return EvalReport(metadata=metadata, metrics=metrics, runs=scored_runs)


def _dump_stage(name: str, payload: dict) -> None:
    """Проміжні артефакти: підкрутив суддю — не платиш за екстракцію вдруге."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")


async def _main(limit: int, concurrency: int) -> None:
    cases = load_eval_dataset(DATASET_PATH)
    if limit:
        cases = cases[:limit]
    logger.info("extraction eval v2: %d постів, %d учасників", len(cases), len(PARTICIPANTS))

    by_model: dict[str, dict[str, ExtractionResult]] = {}
    costs: dict[str, float | None] = {}
    for model_id in PARTICIPANTS:
        by_model[model_id], costs[model_id] = await run_participants(cases, model_id, concurrency)

    _dump_stage(
        "extractions.json",
        {m: {pid: r.model_dump(mode="json") for pid, r in res.items()} for m, res in by_model.items()},
    )

    subsample = determinism_subsample(cases)
    determinism: dict[str, float | None] = {}
    for model_id in PARTICIPANTS:
        second, _ = await run_participants(subsample, model_id, concurrency)
        first = {case.id: by_model[model_id][case.id] for case in subsample if case.id in by_model[model_id]}
        determinism[model_id] = determinism_score(first, second)

    judge = LLMJudge(build_eval_llm(JUDGE_MODEL, temperature=None), judge_id=JUDGE_MODEL)
    scores, judgements = await judge_all_posts(cases, by_model, judge)
    _dump_stage("pooling.json", {j.post_id: j.model_dump(mode="json") for j in judgements})

    report = build_report(
        cases, scores, judgements, PARTICIPANTS, BASELINE_MODEL, JUDGE_MODEL, determinism, costs
    )
    write_report(report, OUT_DIR)
    print(f"winner={report.metrics.winner} decided_by={report.metrics.decided_by}")
    print(f"report → {OUT_DIR}/report.md")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Extraction eval v2")
    parser.add_argument("--limit", type=int, default=0, help="лише перші N постів (0 = усі)")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(_main(args.limit, args.concurrency))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run and confirm it passes**

Run: `.venv/bin/python -m pytest tests/test_eval_v2_runner.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run the whole suite and both linters**

```bash
.venv/bin/python -m pytest tests/ -q && .venv/bin/ruff check . && .venv/bin/complexipy --diff HEAD --ratchet src
```
Expected: all pass, no new function over the complexity threshold.

- [ ] **Step 6: Commit**

```bash
git add scripts/extraction/eval_v2/extraction_eval.py tests/test_eval_v2_runner.py && git commit -m "feat(eval-v2): композиція прогону й звіт"
```

---

## Task 13: Dataset builder

**Scope:** The upstream script that collects ~150 posts from three channels into the dated dataset artifact. Separate run, separate commit, and **blocked on step 0** — it cannot run until the Telegram session is restored by hand.

**Files:**
- Create: `scripts/extraction/build_eval_dataset.py`
- Test: `tests/test_build_eval_dataset.py`

**Interfaces:**
- Consumes: `TelegramSource` (`sources/telegram.py`), `PredictionExtractor` as the prefilter detector.
- Produces: `scripts/data/extraction/eval_dataset_YYYY-MM-DD.json` matching the shape in the design.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_build_eval_dataset.py
import json

import pytest

from extraction.build_eval_dataset import (
    build_payload,
    load_excluded_v1_ids,
    post_id_for,
    split_strata,
)


def test_excluded_ids_come_from_the_v1_artifact(tmp_path):
    path = tmp_path / "v1.json"
    path.write_text(
        json.dumps({"extractions": {"model-a": {"p1": [], "p2": []}, "model-b": {"p2": []}}}),
        encoding="utf-8",
    )

    assert load_excluded_v1_ids(path) == {"p1", "p2"}


def test_empty_exclusion_list_fails_loud(tmp_path):
    """Порожній список виключень означає зламаний артефакт, а не «нема що виключати»."""
    path = tmp_path / "v1.json"
    path.write_text(json.dumps({"extractions": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="виключень"):
        load_excluded_v1_ids(path)


def test_post_id_matches_the_corpus_format():
    assert post_id_for("@O_Arestovich_official", 7683) == "O_Arestovich_official_7683"


def test_split_strata_respects_the_ratio():
    prefilter, random_part = split_strata(list(range(100)), prefilter_share=0.7, seed=1)

    assert len(prefilter) == 70
    assert len(random_part) == 30
    assert not set(prefilter) & set(random_part)


def test_payload_counts_authors_and_strata():
    posts = [
        {"id": "a1", "author": "Арестович", "stratum": "prefilter"},
        {"id": "a2", "author": "Арестович", "stratum": "random"},
        {"id": "k1", "author": "Кущ", "stratum": "prefilter"},
    ]

    payload = build_payload(posts, channels=["@a"], prefilter_model="m")

    assert payload["metadata"]["distribution"]["by_author"] == {"Арестович": 2, "Кущ": 1}
    assert payload["metadata"]["distribution"]["by_stratum"] == {"prefilter": 2, "random": 1}
    assert payload["metadata"]["selection_method"] == "prefilter+random"
    assert len(payload["metadata"]["excluded_v1_ids"]) == 97
    assert json.dumps(payload)  # серіалізується
```

- [ ] **Step 2: Run and confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_build_eval_dataset.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# scripts/extraction/build_eval_dataset.py
"""Крок 1: збірка датасету eval v2 з трьох каналів.

Окремий запуск, окремий артефакт. Блокується кроком 0 — без живої
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

from telethon import TelegramClient  # noqa: E402

from prophet_checker.analysis.extractor import PredictionExtractor  # noqa: E402
from prophet_checker.config import Settings  # noqa: E402
from prophet_checker.models.domain import PersonSource, SourceType  # noqa: E402
from prophet_checker.sources.telegram import TelegramSource  # noqa: E402

from eval_common.clients import build_eval_llm  # noqa: E402

logger = logging.getLogger(__name__)

# (канал, автор, скільки постів у датасет) — обґрунтування вибору авторів у дизайні
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
    """97 контамінованих постів v1 — беруться з артефакту прогону, не переписуються руками."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    extractions = payload["extractions"]
    excluded: set[str] = set()
    for per_post in extractions.values():
        excluded.update(per_post)
    if not excluded:
        raise ValueError(f"{path}: не знайдено жодного id v1 — список виключень не може бути порожнім")
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


def build_payload(posts: list[dict], channels: list[str], prefilter_model: str) -> dict:
    """Артефакт датасету. Список виключень лежить усередині — не лише в дизайні."""
    by_author: dict[str, int] = {}
    by_stratum: dict[str, int] = {}
    for post in posts:
        by_author[post["author"]] = by_author.get(post["author"], 0) + 1
        by_stratum[post["stratum"]] = by_stratum.get(post["stratum"], 0) + 1

    return {
        "metadata": {
            "collected_at": datetime.now(UTC).date().isoformat(),
            "channels": channels,
            "selection_method": "prefilter+random",
            "prefilter_model": prefilter_model,
            "distribution": {"by_author": by_author, "by_stratum": by_stratum},
            "excluded_v1_ids": sorted(load_excluded_v1_ids()),
        },
        "posts": posts,
    }


async def _fetch_candidates(
    source: TelegramSource, channel: str, author: str, limit: int, excluded: set[str]
) -> list[dict]:
    """Сирі кандидати каналу: контаміновані id відсікаються тут, до будь-якого LLM-виклику."""
    person_source = PersonSource(
        id=f"eval-v2-{channel}",
        person_id=author,
        source_type=SourceType.TELEGRAM,
        source_identifier=channel,
    )
    candidates: list[dict] = []
    async for document in source.collect(person_source, limit=limit):
        message_id = document.id.rsplit(":", 1)[-1]
        post_id = post_id_for(channel, int(message_id))
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
    """Гонить детектор по кандидатах, доки не набере target позитивних. Решта — на random."""
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
    channel: str,
    author: str,
    target: int,
    excluded: set[str],
    seed: int,
) -> list[dict]:
    """~70% постів через префільтр-детектор, ~30% випадкових без жодного фільтра."""
    candidates = await _fetch_candidates(
        source, channel, author, target * CANDIDATE_MULTIPLIER, excluded
    )
    random.Random(seed).shuffle(candidates)

    n_prefilter = round(target * PREFILTER_SHARE)
    prefiltered, rest = await _pick_prefiltered(extractor, candidates, n_prefilter)
    n_random = target - len(prefiltered)

    posts = []
    for post in prefiltered:
        posts.append({**post, "stratum": "prefilter"})
    for post in rest[:n_random]:
        posts.append({**post, "stratum": "random"})
    logger.info("channel %s: prefilter=%d random=%d", channel, len(prefiltered), n_random)
    return posts


async def _main(seed: int, out_path: Path) -> None:
    settings = Settings()
    excluded = load_excluded_v1_ids()
    logger.info("виключено %d контамінованих постів v1", len(excluded))

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
        for channel, author, target in CHANNELS:
            posts.extend(
                await collect_channel(
                    source, extractor, channel, author, target, excluded, seed
                )
            )
    finally:
        await client.disconnect()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(posts, [channel for channel, _, _ in CHANNELS], PREFILTER_MODEL)
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
```

`collect_channel` and `_main` touch the network and are not unit-tested; everything above them is pure and is covered by Step 1. After running, update `DATASET_PATH` in `dataset.py` to the produced filename.

- [ ] **Step 4: Run and confirm the tests pass**

Run: `.venv/bin/python -m pytest tests/test_build_eval_dataset.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/build_eval_dataset.py tests/test_build_eval_dataset.py && git commit -m "feat(eval-v2): збірка датасету з трьох каналів"
```

---

## Running it for real

Ordered, and the first item is a human step.

1. **You:** restore the Telegram session by hand. Nothing downstream of this runs without it (design, step 0 — `AuthKeyDuplicatedError`, one auth-key lived on two hosts).
2. `.venv/bin/python scripts/extraction/build_eval_dataset.py` → writes `scripts/data/extraction/eval_dataset_YYYY-MM-DD.json`. Update `DATASET_PATH` in `dataset.py` to the produced filename.
3. Smoke each participant id before the paid run — an unverified LiteLLM id must fail on post one, not post 150:
   ```bash
   .venv/bin/python scripts/extraction/eval_v2/extraction_eval.py --limit 2
   ```
4. Full run: `.venv/bin/python scripts/extraction/eval_v2/extraction_eval.py`
5. Read `report.md`, then set the two parked constants in `metrics.py` from the first run: `NOISE_BAND` (P2, from the determinism re-run spread) and `PRECISION_GATE_MARGIN` (P3, from the baseline's own precision). Re-run selection only — no re-extraction needed.
6. Human audit of ~20 posts (design, step 8). Not automated in this plan; the audit slice is drawn from `report.json` and stored with post ids and a date.

## What is deliberately not in this plan

- The human-audit tooling and the accumulated gold store (design P6) — the first run has to exist before the audit format is worth fixing.
- `report.md` formatting beyond what `eval_common.write_report` already produces.
- Any change to `EXTRACTION_SYSTEM` or the `Prediction` shape. Those are above this rung; the `prediction_date` and `target_date` questions live in chips `task_95a2d0be` and `task_c8d43374`.
