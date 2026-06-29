# RAG Answer Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Переписати `RAG_SYSTEM` + `RAG_TEMPLATE` так, щоб відповідь була природною прозою «прогноз → вердикт» (статус простою мовою), без службових полів (UUID/confidence/сирий enum/вигадані стати).

**Architecture:** Промпт-онлі зміна за [дизайном](2026-06-29-rag-answer-contract-design.md). Чіпаємо лише дві константи в `prompts.py`; сигнатури `build_rag_prompt`/`render_predictions` і поданий моделі контекст — без змін. Не-лік досягається інструкцією; eval (із вже-вкладеним фіксом судді) + manual — петля підтвердження.

**Tech Stack:** Python 3.14, pytest (`asyncio_mode=auto`), ruff (line 100). Venv `.venv`. Тести: `.venv/bin/python -m pytest tests/ -q`.

**Гілка:** нова feature-гілка від `main` (напр. `rag-answer-contract`), як решта прод-змін; merge у `main` по завершенні. **Не** на `generation-eval-v2` (то окремий трек).

**Коміти:** conventional commits українською.

---

## File structure

| Файл | Роль | Зміна |
|------|------|-------|
| `src/prophet_checker/llm/prompts.py` | прод-промпти | переписати `RAG_SYSTEM` + `RAG_TEMPLATE` під контракт |
| `tests/test_llm_prompts.py` | тест | guard-тест: лік-директиви прибрані, переклад статусу присутній |

Жоден інший файл не зачеплено: `build_rag_prompt`/`render_predictions` (сигнатури й вихід контексту незмінні), `answer_orchestrator` (імпортує `RAG_SYSTEM`, поведінка та сама), eval (юзає `FAITHFULNESS_SYSTEM`, не `RAG_SYSTEM`).

---

## Task 1: Переписати RAG_SYSTEM + RAG_TEMPLATE під контракт

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py` (константи `RAG_SYSTEM`, `RAG_TEMPLATE`, рядки 196–212)
- Test: `tests/test_llm_prompts.py`

- [ ] **Step 1: Додати guard-тест**

У `tests/test_llm_prompts.py` додай (поряд з `test_build_rag_prompt`):

```python
def test_rag_prompt_contract_drops_leak_directives():
    from prophet_checker.llm.prompts import RAG_SYSTEM, RAG_TEMPLATE

    combined = RAG_SYSTEM + RAG_TEMPLATE
    # старі директиви, що провокували лік службових полів, прибрані
    assert "confidence scores" not in combined
    assert "accuracy statistics" not in combined
    # контракт присутній: переклад 4 статусів у людський вердикт
    assert "прогноз справдився" in RAG_SYSTEM
    assert "прогноз не справдився" in RAG_SYSTEM
    assert "оцінити не вдалося" in RAG_SYSTEM
    assert "ще зарано" in RAG_SYSTEM
```

- [ ] **Step 2: Запусти — має впасти (RED)**

Run: `.venv/bin/python -m pytest tests/test_llm_prompts.py::test_rag_prompt_contract_drops_leak_directives -q`
Expected: FAIL — старий `RAG_SYSTEM` містить «Always cite sources and confidence scores» (asсert `"confidence scores" not in combined` падає) і не містить вердикт-фраз.

- [ ] **Step 3: Переписати константи**

У `src/prophet_checker/llm/prompts.py` заміни поточні `RAG_SYSTEM` і `RAG_TEMPLATE` на:

```python
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

Do NOT put in the answer: internal source IDs, the confidence number, the raw English status label
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
predictions into one coherent answer. Keep it short. No internal IDs, no confidence numbers, no raw
status labels, no invented statistics. End with the single disclaimer line."""
```

Нічого більше в файлі не чіпай. `RAG_TEMPLATE` зберігає плейсхолдери `{question}` і `{predictions_context}` — тож `build_rag_prompt(...).format(...)` працює як раніше.

- [ ] **Step 4: Запусти — GREEN + наявний тест + сюїта**

Run: `.venv/bin/python -m pytest tests/test_llm_prompts.py -q && .venv/bin/python -m pytest tests/ -q`
Expected: PASS. Зокрема наявний `test_build_rag_prompt` лишається зеленим — він перевіряє наявність `question`/claim/`id`/дати/`refuted` у виводі `build_rag_prompt`, а ті беруться з `render_predictions(sources)` (контекст не змінювали) + `{question}`.

- [ ] **Step 5: Lint + commit**

```bash
.venv/bin/ruff check src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py
git add src/prophet_checker/llm/prompts.py tests/test_llm_prompts.py
git commit -m "feat(rag): переписати RAG-промпт під контракт прогноз→вердикт (без службових полів)"
```

---

## Verification (після merge, потребує твоєї інфри)

Юніт-тести **не** перевіряють стиль виводу LLM (це вихід моделі). Реальна перевірка — за дизайном:

1. **Перепрогін generation-eval** (на гілці з фіксом судді — спільний `render_predictions`):
   `.venv/bin/python scripts/generation/generation_eval.py --limit 20 --concurrency 4`
   Очікування: faithfulness ~0.95+ (нема metadata-echo), вигадані стати зникли.
2. **Manual-інспекція** 3–5 відповідей у `scripts/outputs/generation_eval/report.json`: проза, явний вердикт простою мовою, **нема** UUID / числа confidence / сирого «premature» / вигаданих стат / багаторядкового дисклеймера.

**Якщо лік лишиться** (модель не слухається) → гартування з дизайну (поза цим планом): трим `id`/`confidence` з `render_predictions`, або no-leakage eval-метрик.

---

## Готово, коли

- guard-тест зелений; `test_build_rag_prompt` і вся сюїта зелені; ruff чисто.
- `RAG_SYSTEM`/`RAG_TEMPLATE` несуть контракт прогноз→вердикт без лік-директив.
- (після merge) перепрогін евалу + manual підтверджують чистий стиль.
