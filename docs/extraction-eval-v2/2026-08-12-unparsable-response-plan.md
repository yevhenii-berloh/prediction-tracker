# Unparsable extraction response — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop an unparsable extraction response from looking like "the model found no predictions", so neither production nor the eval treats a broken answer as a legitimate empty one.

**Architecture:** `parse_extraction_response` raises on a payload it cannot read, instead of returning `[]`. `PredictionExtractor.extract()` catches that in its own `try` and returns `ExtractionOutcome(failed=True)`, keeping `extract()` total — it still never raises. Everything downstream already handles `failed`: ingestion halts the channel without moving the cursor (`87d330c`), and the eval marks that model/post unmeasurable (`00ad1e6`).

**Tech Stack:** Python 3.14, Pydantic v2, pytest (`asyncio_mode = "auto"`). No new dependencies.

**Why now:** the 10-post smoke on 2026-08-12 scored `gemini/gemini-3.5-flash-lite` at 0 claims across all 10 posts and `coverage` 0.000. The cause was envelope shape, not extraction ability — fixed for the bare-array case in `513d888`. That fix removes one shape; it does not remove the class. Any other shape a model invents still reads as silence.

## Global Constraints

- Red zone: every task touches `src/`. One commit per task, never mixed with green-zone work.
- `extract()` must remain total — it never raises. Ingestion depends on that; a parse failure surfacing as `"halted at step=processing: ..."` would misattribute the cause.
- Ruff line length 100; `complexipy` threshold 12, ratchet mode. `parse_extraction_response` is currently 2.
- Tests are async-first with no marker; nothing touches the network.
- Commit messages in Ukrainian, conventional-commit prefixes.

---

## Task 1: The parser refuses shapes it cannot read

**Scope:** `parse_extraction_response` currently swallows every failure into `[]`. Make it raise `ValueError` for a payload that is neither a bare list nor a dict carrying `predictions`, while keeping both legitimate empty forms working. **Red zone — own commit.**

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py:501`
- Test: `tests/test_llm_prompts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_extraction_response(response) -> list[dict]`, raising `ValueError` on an unreadable payload.

- [ ] **Step 1: Rewrite the two tests that encode the old contract**

Both currently assert `== []` and must now assert a raise. They are the bug, written down:

```python
def test_parse_extraction_response_invalid_json():
    """Нерозбірлива відповідь — не «нічого не знайдено»."""
    with pytest.raises(ValueError):
        parse_extraction_response("not json at all")


def test_parse_extraction_response_rejects_a_dict_without_predictions():
    """Систематична розбіжність ключа інакше тихо оцінює модель у нуль назавжди."""
    with pytest.raises(ValueError):
        parse_extraction_response(json.dumps({"claims": [{"claim_text": "x"}]}))


def test_parse_extraction_response_rejects_a_scalar():
    with pytest.raises(ValueError):
        parse_extraction_response(json.dumps("просто рядок"))


def test_parse_extraction_response_keeps_both_empty_forms():
    """Порожньо — це валідна відповідь в обох конвертах."""
    assert parse_extraction_response(json.dumps({"predictions": []})) == []
    assert parse_extraction_response(json.dumps([])) == []
```

- [ ] **Step 2: Run them and confirm the three raise-tests fail**

Run: `.venv/bin/python -m pytest tests/test_llm_prompts.py -k parse_extraction -v`
Expected: three FAIL with `DID NOT RAISE`, the empty-forms test PASSES.

- [ ] **Step 3: Implement**

```python
def parse_extraction_response(response: str) -> list[dict]:
    """Розібрати відповідь екстрактора. Кидає ValueError, якщо форма нечитабельна.

    Порожній результат і нерозбірлива відповідь — різні речі. Поки вони обидві
    були `[]`, модель зі зламаним конвертом виглядала як модель, що нічого не
    знайшла: саме так gemini-3.5-flash-lite отримав coverage 0.000 на смоуку.
    """
    try:
        data = json.loads(_strip_code_fence(response))
    except json.JSONDecodeError as exc:
        raise ValueError(f"відповідь екстрактора не є JSON: {exc}") from exc

    # Конверт плаває навіть у межах однієї моделі — обидві форми легітимні
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "predictions" in data:
        return data["predictions"]
    raise ValueError(f"невідома форма відповіді екстрактора: {type(data).__name__}")
```

- [ ] **Step 4: Run the file and the ratchet**

```bash
.venv/bin/python -m pytest tests/test_llm_prompts.py -q && .venv/bin/complexipy --diff HEAD --ratchet src
```
Expected: all pass; `parse_extraction_response` rises from 2 to ~5, far below the threshold.

- [ ] **Step 5: Commit**

```bash
git add src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py && git commit -m "fix(prompts): нерозбірлива відповідь екстракції кидає, а не мовчить"
```

---

## Task 2: `extract()` turns a parse failure into `failed=True`

**Scope:** Task 1 makes the parser raise, which would propagate out of `extract()` and break its totality. Wrap the parse in its own `try` so a bad payload becomes `ExtractionOutcome(failed=True)` — the same signal a dead API already produces. **Red zone — own commit.**

**Files:**
- Modify: `src/prophet_checker/analysis/extractor.py:50`
- Test: `tests/test_analysis_extractor.py`

**Interfaces:**
- Consumes: `parse_extraction_response` raising (Task 1).
- Produces: `extract()` returns `ExtractionOutcome(failed=True, error="UnparsableResponse")`; still never raises.

- [ ] **Step 1: Write the failing tests**

```python
async def test_unparsable_response_is_a_failure_not_an_empty_result():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value="це не json")
    extractor = PredictionExtractor(llm)

    outcome = await extractor.extract(
        text="текст",
        person_id="p",
        document_id="d",
        person_name="Арестович",
        published_date="2026-06-01",
    )

    assert outcome.failed is True
    assert outcome.error == "UnparsableResponse"
    assert outcome.predictions == []


async def test_extract_never_raises_on_a_broken_payload():
    """Інжест покладається на тотальність extract(): виняток звідси став би
    «halted at step=processing» і сховав справжню причину."""
    llm = MagicMock()
    llm.complete = AsyncMock(return_value='{"claims": []}')
    extractor = PredictionExtractor(llm)

    outcome = await extractor.extract(
        text="текст",
        person_id="p",
        document_id="d",
        person_name="Арестович",
        published_date="2026-06-01",
    )

    assert outcome.failed is True
```

- [ ] **Step 2: Run and confirm both fail**

Run: `.venv/bin/python -m pytest tests/test_analysis_extractor.py -q`
Expected: FAIL — currently a `ValueError` escapes `extract()`.

- [ ] **Step 3: Implement**

Replace the bare parse call:

```python
        try:
            raw_predictions = parse_extraction_response(response)
        except ValueError:
            # extract() лишається тотальною: інжест ловить failed, а не виняток
            logger.exception("Unparsable extraction response for %s", document_id)
            return ExtractionOutcome(failed=True, error="UnparsableResponse")

        if not raw_predictions:
            return ExtractionOutcome()
```

- [ ] **Step 4: Run the suite**

```bash
.venv/bin/python -m pytest tests/ -q
```
Expected: all pass. `extract()` is grandfathered at complexity 15 — check `complexipy src` shows it has not risen; if it has, lift the parse-and-guard into a helper rather than accepting a higher number.

- [ ] **Step 5: Commit**

```bash
git add src/prophet_checker/analysis/extractor.py tests/test_analysis_extractor.py && git commit -m "fix(extractor): нерозбірлива відповідь → failed, а не порожній результат"
```

---

## Task 3: Confirm the downstream reactions on real data

**Scope:** Both consumers already handle `failed`, but neither has seen a parse-driven failure. Verify the two behaviours end to end rather than assuming they compose.

**Files:**
- Test: `tests/test_ingestion_orchestrator.py`, `tests/test_eval_v2_runner.py`

- [ ] **Step 1: Add one test per consumer**

```python
# tests/test_ingestion_orchestrator.py — курсор не рухається на нерозбірливій відповіді
async def test_unparsable_response_halts_the_channel_like_any_failure():
    ...  # той самий каркас, що в test_failed_extraction_does_not_advance_the_cursor,
         # але llm.complete повертає "не json" замість extract-мока
    assert updated[0].last_collected_at == datetime(2024, 1, 1, tzinfo=UTC)
```

```python
# tests/test_eval_v2_runner.py — модель зі зламаним конвертом не карається по coverage
async def test_unparsable_model_is_unmeasurable_not_silent():
    by_model = {
        "base": {"p1": _result("Ціни зростуть")},
        "broken_envelope": {"p1": ExtractionResult(predictions=[], failed=True)},
    }
    ...
    assert report.metrics.per_model["broken_envelope"].coverage is None
    assert report.metrics.health.extraction_failures["broken_envelope"] == 1
```

- [ ] **Step 2: Run both files**

```bash
.venv/bin/python -m pytest tests/test_ingestion_orchestrator.py tests/test_eval_v2_runner.py -q
```
Expected: all pass without further production changes. If either fails, the composition is broken and that is the finding.

- [ ] **Step 3: Commit**

```bash
git add tests/ && git commit -m "test: нерозбірлива відповідь доходить до інжесту й до евалу"
```

---

## Verification on live models

After Task 2, re-run the smoke and compare against the run of 2026-08-12:

```bash
.venv/bin/python scripts/extraction/eval_v2/extraction_eval.py --limit 10
```

Two outcomes are informative, and both are wins over today:

- `gemini-3.5-flash-lite` produces real claims → the bare-array fix was the whole story, and it becomes a genuine candidate.
- It reports `extraction_failures` in the health block → it emits a third envelope shape we have not seen, now visible as a number instead of a silent zero.

The failure mode to watch for is a *rise* in `extraction_failures` across models that were fine before. That would mean the parser is now too strict, and the fix is to widen the accepted shapes in Task 1, not to loosen the raise.

## What is deliberately not in this plan

- Coaxing models into a stable envelope by changing `EXTRACTION_SYSTEM`. That is a production prompt change and sits above this rung; the eval measures models against the current rubric.
- A retry-on-unparsable inside `extract()`. `LLMClient` already retries transport 3×, and a shape problem usually repeats. Worth revisiting only if the first full run shows a model failing intermittently rather than systematically.
- Anything about the `situation` field. A prediction that parses but lacks `situation` is dropped by `extract()` today, and that is a rubric decision, not a parsing one.
