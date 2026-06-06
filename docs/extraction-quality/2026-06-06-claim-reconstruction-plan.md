# Реконструкція claim_text — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development або superpowers:executing-plans. Кроки — checkbox (`- [ ]`).

**Goal:** Прибрати фрагменти-списку з claim_text — екстрактор має повертати самодостатні фальсифіковані твердження мовою оригіналу з вірною полярністю автора.

**Architecture:** Тільки промт. Правимо два рядкові константи в `src/prophet_checker/llm/prompts.py` (`EXTRACTION_TEMPLATE` + `EXTRACTION_SYSTEM`). Без коду/схеми/нових юніт-тестів. Спільний промт → eval/smoke підхоплюють автоматично. Валідація — перевитягом doc 20.

**Tech Stack:** Python 3.12, LiteLLM (Gemini Flash Lite), Postgres (docker `prophet_postgres`).

**Обмеження:** NO docstrings/inline comments. `.venv/bin/python`. cwd `/Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker` (префікс `cd`). Коміти українською. Точний текст промта — з design doc (Компоненти A/B/C), verbatim.

---

### Task 1: Правка промта (claim_text reconstruction)

**Files:**
- Modify: `src/prophet_checker/llm/prompts.py`

- [ ] **Step 1: Переписати поле claim_text у `EXTRACTION_TEMPLATE`**

Замінити рядок:
```
- claim_text: the exact prediction (in original language)
```
на:
```
- claim_text: a SELF-CONTAINED reconstruction of the prediction, in the
  post's original language. Rewrite it as one complete, grammatical,
  falsifiable sentence — explicit subject + predicate + timeframe when known.
  Never copy a bare list item or fragment; never keep list punctuation. The
  sentence must state the AUTHOR'S OWN forecast with its correct polarity
  (whether the author expects the event to HAPPEN or to FAIL / NOT happen).
```

- [ ] **Step 2: Додати блок RECONSTRUCTION & FAITHFULNESS + few-shot у `EXTRACTION_SYSTEM`**

Знайти кінець промта:
```
- Criterion 4: "Would a reader 1 year later actually CARE whether this came true?" If no — it's not substantive.

Respond ONLY with raw JSON — do NOT wrap in markdown code fences."""
```
Вставити новий блок МІЖ рядком Criterion 4 і рядком `Respond ONLY...`, щоб вийшло:
```
- Criterion 4: "Would a reader 1 year later actually CARE whether this came true?" If no — it's not substantive.

RECONSTRUCTION & FAITHFULNESS (how to phrase each extracted claim):

R1. Self-contained form. Each claim_text must be a standalone, grammatical,
    falsifiable sentence in the post's original language. Do NOT output bare
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
RIGHT -> "Процесс поэтапного примирения с РФ через комиссию Ермак–Козак
        (прекращение огня, вода в Крым, выборы в ОРДЛО) в итоге провалится."

Respond ONLY with raw JSON — do NOT wrap in markdown code fences."""
```

- [ ] **Step 3: Прогнати наявні тести — мають лишитись зеленими**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python -m pytest tests/ -q`
Expected: `205 passed` (зміна не чіпає `test_build_extraction_prompt` — він перевіряє підстановки + "JSON"; і `situation`-асерти на рядку 293).

- [ ] **Step 4: Ruff**

Run: `cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/ruff check src/prophet_checker/llm/prompts.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && \
git add src/prophet_checker/llm/prompts.py && \
git commit -m "$(printf 'feat(extraction): реконструкція claim_text замість дослівних фрагментів\n\nclaim_text стає самодостатнім фальсифікованим реченням мовою оригіналу;\nдодано R1/R2/R3 (форма/списки/полярність) + few-shot (doc 20) у\nEXTRACTION_SYSTEM. Прибирає фрагменти-списку на кшталт "вода в Крым;".\n\nCo-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>')"
```

---

### Task 2: Валідація — перевитяг doc 20

**Files:** жодних (перевірка поведінки на реальному LLM, ~$0.001).

- [ ] **Step 1: Дістати сирий текст doc 20 у JSON-обгортку для `run_extraction`**

`scripts/extraction/run_extraction.py` читає вибірку постів із JSON. Найшвидше — викликати екстрактор напряму на тексті doc 20. Дістати текст:

Run:
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && \
docker exec prophet_postgres psql -U prophet -d prophet_checker -t -A \
  -c "SELECT raw_text FROM raw_documents WHERE id='tg:@O_Arestovich_official:20';" > /tmp/doc20.txt && \
wc -l /tmp/doc20.txt
```
Expected: непорожній файл із текстом посту (перелік «вех» комісії Ермак-Козак).

- [ ] **Step 2: Прогнати екстрактор на doc 20 напряму**

Run:
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && .venv/bin/python - <<'PY'
import asyncio, os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(".env", override=True)
from prophet_checker.analysis.extractor import PredictionExtractor
from prophet_checker.llm.client import LLMClient

text = Path("/tmp/doc20.txt").read_text().strip()
client = LLMClient(provider="gemini", model="gemini-3.1-flash-lite-preview",
                   api_key=os.environ["GEMINI_API_KEY"], temperature=0.0)
ex = PredictionExtractor(client)
preds = asyncio.run(ex.extract(text=text, person_id="p", document_id="tg:@O_Arestovich_official:20",
                               person_name="@O_Arestovich_official", published_date="2020-03-13"))
print(f"\n=== {len(preds)} claims ===")
for p in preds:
    print(f"- {p.claim_text!r}")
PY
```
Expected (eyeball): 1–3 claim-и **російською**, повні речення, **БЕЗ** кінцевих `;`/фрагментів; полярність — процес примирення/«вода в Крим» подається як такий, що **провалиться** (не «станеться»). Якщо досі лізуть фрагменти або інвертована полярність — повернутись до Task 1, підсилити R2/R3 (НЕ додавати програмний фільтр без рішення користувача).

- [ ] **Step 3: Spot-check ще 2 списко-подібних постів**

Run (знайти кандидатів зі списками):
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && \
docker exec prophet_postgres psql -U prophet -d prophet_checker -t -A \
  -c "SELECT id FROM raw_documents WHERE raw_text LIKE '%;%' AND length(raw_text) > 600 LIMIT 3;"
```
Повторити Step 2 для 1–2 з них (підмінивши id/текст). Expected: claim-и — повні речення, без фрагментів. Це ручна перевірка, не gate; зафіксувати спостереження в коментарі до коміту Task 3 або усно.

---

### Task 3: Backfill — перезатерти junk doc 20

**Files:** жодних (операція над БД).

- [ ] **Step 1: Видалити 9 junk-claim-ів doc 20**

Run:
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && \
docker exec prophet_postgres psql -U prophet -d prophet_checker \
  -c "DELETE FROM predictions WHERE document_id='tg:@O_Arestovich_official:20';"
```
Expected: `DELETE 9`.

- [ ] **Step 2: Скинути курсор так, щоб doc 20 знову потрапив у перевитяг (опційно)**

Перевитяг через `run_ingestion` йде від `last_collected_at`. doc 20 — від 2020-03-13. Якщо курсор уже далі, простіше перезаписати лише цей пост напряму. Дістати reconstructed-claim-и з Task 2 Step 2 і вставити вручну АБО прогнати точковий ingest:

Run (точковий ingest від дати ДО doc 20, limit малий):
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && \
docker exec prophet_postgres psql -U prophet -d prophet_checker \
  -c "UPDATE person_sources SET last_collected_at = '2020-03-12 00:00:00+00' WHERE id='ps:@O_Arestovich_official';" && \
.venv/bin/python scripts/ingestion/run_ingestion.py --channel @O_Arestovich_official --limit 3
```
Expected: лог `ingestion ps:@O_Arestovich_official done: ...` ; doc 20 перевитягнуто з reconstructed-claim-ами.

- [ ] **Step 3: Перевірити результат у БД**

Run:
```bash
cd /Users/evgenijberlog/Claude/Brain/Brain/prediction-tracker && \
docker exec prophet_postgres psql -U prophet -d prophet_checker -t -A \
  -c "SELECT claim_text FROM predictions WHERE document_id='tg:@O_Arestovich_official:20';"
```
Expected: повні речення російською, без `;`-фрагментів. Якщо doc 20 не зібрався (курсор/ліміт) — повторити Step 2 з більшим `--limit` або нижчою датою.

---

## Self-Review

**Spec coverage:** Компонент A → Task 1 Step 1 ✓; Компонент B (R1/R2/R3) + C (few-shot) → Task 1 Step 2 ✓; Валідація (1) doc 20 → Task 2 ✓; (2) spot-check → Task 2 Step 3 ✓; (3) backfill → Task 3 ✓; «205 тестів зелені» → Task 1 Step 3 ✓; ризик контракту → перевіряється eyeball-ом у Task 2.

**Placeholders:** немає — увесь текст промта verbatim, усі команди конкретні.

**Type consistency:** немає коду/сигнатур; `PredictionExtractor.extract(text, person_id, document_id, person_name, published_date)` збігається з фактичною сигнатурою (`analysis/extractor.py`).

**Ризик у Task 3 Step 2:** редагування курсора — операційний хак для перевитягу одного посту; безпечний на smoke-БД. Якщо незручно — альтернатива: ручний `INSERT` reconstructed-claim-ів з Task 2.
