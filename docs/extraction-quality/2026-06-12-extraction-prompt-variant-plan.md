# План: вибір промпт-варіанта екстракції в extraction_quality_eval

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Аргумент `--extraction-prompt <шлях>` в `extraction_quality_eval.py` підставляє альтернативний system prompt екстракції (дефолт — продакшн), з простежуваністю ім'я+sha256 в артефакті та run plan.

**Architecture:** Опціональний параметр `system_prompt` прокидається ланцюжком `extraction_quality_eval` → `_default_extractor_factory` → `PredictionExtractor`; кожна ланка має дефолт "як зараз", тож продакшн-шлях (`factory.py`) не змінюється. Варіанти — файли в `scripts/data/prompts/`.

**Tech Stack:** Python 3.14, pytest (asyncio_mode=auto, моки `AsyncMock`), наявні скрипти `scripts/extraction/`.

**Спека:** [2026-06-12-extraction-prompt-variant-design.md](2026-06-12-extraction-prompt-variant-design.md)

Усі команди — з кореня репо. Тести: `.venv/bin/python -m pytest`.

---

### Task 1: `PredictionExtractor` приймає `system_prompt`

**Files:**
- Modify: `src/prophet_checker/analysis/extractor.py` (конструктор + виклик `complete`)
- Test: `tests/test_analysis_extractor.py`

- [ ] **Step 1: Додати failing-тести** в кінець `tests/test_analysis_extractor.py` (у файлі вже є хелпер `make_llm` і константа `LLM_RESPONSE_ONE` — використати їх):

```python
async def test_extract_uses_production_system_prompt_by_default():
    from prophet_checker.llm.prompts import get_extraction_system

    llm = make_llm(LLM_RESPONSE_ONE)
    extractor = PredictionExtractor(llm)
    await extractor.extract(
        text="T", person_id="p", document_id="d",
        person_name="Арестович", published_date="2023-01-15",
    )
    assert llm.complete.call_args.kwargs["system"] == get_extraction_system()


async def test_extract_uses_system_prompt_override():
    llm = make_llm(LLM_RESPONSE_ONE)
    extractor = PredictionExtractor(llm, system_prompt="CUSTOM PROMPT")
    await extractor.extract(
        text="T", person_id="p", document_id="d",
        person_name="Арестович", published_date="2023-01-15",
    )
    assert llm.complete.call_args.kwargs["system"] == "CUSTOM PROMPT"
```

- [ ] **Step 2: Переконатися, що тести падають**

Run: `.venv/bin/python -m pytest tests/test_analysis_extractor.py -q`
Expected: FAIL — `TypeError: PredictionExtractor.__init__() got an unexpected keyword argument 'system_prompt'`

- [ ] **Step 3: Імплементація** в `src/prophet_checker/analysis/extractor.py`:

```python
# Було:
    def __init__(self, llm) -> None:
        self._llm = llm

# Стало:
    def __init__(self, llm, system_prompt: str | None = None) -> None:
        self._llm = llm
        self._system_prompt = system_prompt
```

і у `extract()`:

```python
# Було:
            response = await self._llm.complete(prompt, system=get_extraction_system())

# Стало:
            response = await self._llm.complete(
                prompt, system=self._system_prompt or get_extraction_system()
            )
```

- [ ] **Step 4: Тести проходять**

Run: `.venv/bin/python -m pytest tests/test_analysis_extractor.py -q`
Expected: PASS (усі)

- [ ] **Step 5: Commit**

```bash
git add src/prophet_checker/analysis/extractor.py tests/test_analysis_extractor.py
git commit -m "feat(extraction): PredictionExtractor приймає system_prompt override"
```

---

### Task 2: `_default_extractor_factory` прокидає `system_prompt`

**Files:**
- Modify: `scripts/extraction/detection_eval.py` (`_default_extractor_factory`, ~рядок 208)
- Test: `tests/test_extraction_quality_eval.py`

- [ ] **Step 1: Failing-тест** у кінець `tests/test_extraction_quality_eval.py` (стиль файлу — імпорти всередині тесту; ключ API мокаємо через monkeypatch):

```python
def test_default_extractor_factory_passes_system_prompt(monkeypatch):
    from extraction.detection_eval import _default_extractor_factory

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    extractor = _default_extractor_factory(
        "gemini/gemini-3.1-flash-lite-preview", system_prompt="CUSTOM"
    )
    assert extractor._system_prompt == "CUSTOM"

    extractor_default = _default_extractor_factory("gemini/gemini-3.1-flash-lite-preview")
    assert extractor_default._system_prompt is None
```

- [ ] **Step 2: Переконатися, що тест падає**

Run: `.venv/bin/python -m pytest tests/test_extraction_quality_eval.py::test_default_extractor_factory_passes_system_prompt -q`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'system_prompt'`

- [ ] **Step 3: Імплементація** в `scripts/extraction/detection_eval.py`:

```python
# Було:
def _default_extractor_factory(model_id: str) -> PredictionExtractor:

# Стало:
def _default_extractor_factory(
    model_id: str, system_prompt: str | None = None
) -> PredictionExtractor:
```

і в кінці функції:

```python
# Було:
    return PredictionExtractor(client)

# Стало:
    return PredictionExtractor(client, system_prompt=system_prompt)
```

- [ ] **Step 4: Тест проходить**

Run: `.venv/bin/python -m pytest tests/test_extraction_quality_eval.py::test_default_extractor_factory_passes_system_prompt -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/detection_eval.py tests/test_extraction_quality_eval.py
git commit -m "feat(extraction): _default_extractor_factory прокидає system_prompt"
```

---

### Task 3: Хелпер `_resolve_extraction_prompt` (текст + метадані)

**Files:**
- Modify: `scripts/extraction/extraction_quality_eval.py` (нова функція в секції "CLI orchestration", після `_load_filtered_posts`; нові імпорти)
- Test: `tests/test_extraction_quality_eval.py`

- [ ] **Step 1: Failing-тести:**

```python
def test_resolve_extraction_prompt_default_is_production():
    from extraction.extraction_quality_eval import _resolve_extraction_prompt
    from prophet_checker.llm.prompts import get_extraction_system
    import hashlib

    override, meta = _resolve_extraction_prompt(None)
    assert override is None
    assert meta["extraction_prompt"] == "production"
    expected_sha = hashlib.sha256(get_extraction_system().encode()).hexdigest()[:12]
    assert meta["extraction_prompt_sha256"] == expected_sha


def test_resolve_extraction_prompt_reads_file(tmp_path):
    from extraction.extraction_quality_eval import _resolve_extraction_prompt
    import hashlib

    f = tmp_path / "variant.md"
    f.write_text("VARIANT PROMPT", encoding="utf-8")
    override, meta = _resolve_extraction_prompt(str(f))
    assert override == "VARIANT PROMPT"
    assert meta["extraction_prompt"] == str(f)
    assert meta["extraction_prompt_sha256"] == hashlib.sha256(b"VARIANT PROMPT").hexdigest()[:12]
```

- [ ] **Step 2: Переконатися, що тести падають**

Run: `.venv/bin/python -m pytest tests/test_extraction_quality_eval.py -q -k resolve_extraction_prompt`
Expected: FAIL — `ImportError: cannot import name '_resolve_extraction_prompt'`

- [ ] **Step 3: Імплементація.** У `scripts/extraction/extraction_quality_eval.py` додати до імпортів `import hashlib` (поряд з `import json`) та `from prophet_checker.llm.prompts import get_extraction_system  # noqa: E402` (поряд з імпортом `LLMClient`). Після `_load_filtered_posts` додати:

```python
def _resolve_extraction_prompt(path: str | None) -> tuple[str | None, dict[str, str]]:
    """Resolve --extraction-prompt: (override_text | None, metadata).

    None (дефолт) = продакшн-промпт із prompts.py — override не передається,
    але sha256 рахується, щоб артефакт фіксував, ЩО саме було проганяно.
    """
    if path is None:
        text = get_extraction_system()
        name = "production"
        override = None
    else:
        text = Path(path).read_text(encoding="utf-8")
        name = str(path)
        override = text
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return override, {"extraction_prompt": name, "extraction_prompt_sha256": sha}
```

- [ ] **Step 4: Тести проходять**

Run: `.venv/bin/python -m pytest tests/test_extraction_quality_eval.py -q -k resolve_extraction_prompt`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/extraction_quality_eval.py tests/test_extraction_quality_eval.py
git commit -m "feat(extraction): _resolve_extraction_prompt — текст варіанта + метадані sha256"
```

---

### Task 4: `run_stage1_extraction` пише prompt-метадані в артефакт

**Files:**
- Modify: `scripts/extraction/extraction_quality_eval.py` (`run_stage1_extraction`: сигнатура ~рядок 180, блок `"metadata"` ~рядок 279)
- Test: `tests/test_extraction_quality_eval.py`

- [ ] **Step 1: Failing-тест** (хелпер `_make_factory` уже є у файлі, група B1):

```python
async def test_stage1_writes_prompt_metadata(tmp_path):
    from extraction.extraction_quality_eval import run_stage1_extraction

    posts = [{"id": "p1", "person_name": "Арестович",
              "published_at": "2024-01-01", "text": "T"}]
    out_path = tmp_path / "extractions.json"
    await run_stage1_extraction(
        extractors=["model_x"],
        posts=posts,
        author_filter="Арестович",
        output_path=out_path,
        extractor_factory=_make_factory({"model_x": {"p1": []}}),
        prompt_metadata={"extraction_prompt": "variant.md",
                         "extraction_prompt_sha256": "abc123def456"},
    )
    artifact = json.loads(out_path.read_text())
    assert artifact["metadata"]["extraction_prompt"] == "variant.md"
    assert artifact["metadata"]["extraction_prompt_sha256"] == "abc123def456"
```

- [ ] **Step 2: Переконатися, що тест падає**

Run: `.venv/bin/python -m pytest tests/test_extraction_quality_eval.py::test_stage1_writes_prompt_metadata -q`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'prompt_metadata'`

- [ ] **Step 3: Імплементація.** У сигнатуру `run_stage1_extraction` (після `per_model_min_interval`) додати:

```python
    prompt_metadata: dict[str, str] | None = None,
```

У блоці запису артефакта:

```python
# Було:
                "metadata": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "dataset_size": len(filtered_posts),
                    "extractors": sorted(extractions.keys()),
                    "author_filter": author_filter,
                },

# Стало:
                "metadata": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "dataset_size": len(filtered_posts),
                    "extractors": sorted(extractions.keys()),
                    "author_filter": author_filter,
                    **(prompt_metadata or {}),
                },
```

- [ ] **Step 4: Тест проходить, наявні тести stage 1 не зламані**

Run: `.venv/bin/python -m pytest tests/test_extraction_quality_eval.py -q`
Expected: PASS (усі)

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/extraction_quality_eval.py tests/test_extraction_quality_eval.py
git commit -m "feat(extraction): prompt-метадані в артефакті extraction_outputs.json"
```

---

### Task 5: CLI `--extraction-prompt` + підключення в `_main_async` + run plan

**Files:**
- Modify: `scripts/extraction/extraction_quality_eval.py` (`_build_arg_parser`; stage-1 блок `_main_async` ~рядок 714)
- Test: `tests/test_extraction_quality_eval.py`

- [ ] **Step 1: Failing-тест на парсер:**

```python
def test_cli_parses_extraction_prompt():
    from extraction.extraction_quality_eval import _build_arg_parser

    args = _build_arg_parser().parse_args(["--extraction-prompt", "scripts/data/prompts/v2.md"])
    assert args.extraction_prompt == "scripts/data/prompts/v2.md"
    assert _build_arg_parser().parse_args([]).extraction_prompt is None
```

- [ ] **Step 2: Переконатися, що тест падає**

Run: `.venv/bin/python -m pytest tests/test_extraction_quality_eval.py::test_cli_parses_extraction_prompt -q`
Expected: FAIL — `unrecognized arguments: --extraction-prompt` (SystemExit)

- [ ] **Step 3: Імплементація.** У `_build_arg_parser()` (після аргумента `--author`):

```python
    parser.add_argument(
        "--extraction-prompt",
        default=None,
        help="Шлях до файлу з альтернативним system prompt екстракції "
        "(дефолт: продакшн-промпт із prompts.py)",
    )
```

У `_main_async`, stage-1 блок:

```python
# Було:
    if 1 in stages:
        print(
            f"Stage 1: extracting with {len(extractors)} models "
            f"on {args.author} posts"
        )
        await run_stage1_extraction(
            extractors=extractors,
            posts=posts,
            author_filter=args.author,
            output_path=extractions_path,
            extractor_factory=_default_extractor_factory,
            per_model_concurrency=CONCURRENCY_OVERRIDES,
            per_model_min_interval=MIN_CALL_INTERVAL_SECONDS,
        )

# Стало:
    if 1 in stages:
        prompt_override, prompt_meta = _resolve_extraction_prompt(args.extraction_prompt)
        print(
            f"  extraction prompt: {prompt_meta['extraction_prompt']} "
            f"({prompt_meta['extraction_prompt_sha256']})",
            flush=True,
        )
        print(
            f"Stage 1: extracting with {len(extractors)} models "
            f"on {args.author} posts"
        )
        await run_stage1_extraction(
            extractors=extractors,
            posts=posts,
            author_filter=args.author,
            output_path=extractions_path,
            extractor_factory=lambda m: _default_extractor_factory(
                m, system_prompt=prompt_override
            ),
            per_model_concurrency=CONCURRENCY_OVERRIDES,
            per_model_min_interval=MIN_CALL_INTERVAL_SECONDS,
            prompt_metadata=prompt_meta,
        )
```

- [ ] **Step 4: Усі тести проходять**

Run: `.venv/bin/python -m pytest tests/test_extraction_quality_eval.py -q`
Expected: PASS (усі)

- [ ] **Step 5: Commit**

```bash
git add scripts/extraction/extraction_quality_eval.py tests/test_extraction_quality_eval.py
git commit -m "feat(extraction): аргумент --extraction-prompt в extraction_quality_eval"
```

---

### Task 6: Тека варіантів + документація

**Files:**
- Create: `scripts/data/prompts/README.md`
- Modify: `scripts/extraction/extraction_quality_eval.md` (таблиця "Вхід" + приклад запуску)

- [ ] **Step 1: Створити теку з README** (`scripts/data/prompts/README.md`):

```markdown
# Промпт-варіанти екстракції

Кандидати system prompt для A/B-тестування через
`extraction_quality_eval.py --extraction-prompt <файл>`.

- Один файл = один повний system prompt (plain text / markdown).
- Дефолт без аргументу — продакшн-промпт `EXTRACTION_SYSTEM`
  із `src/prophet_checker/llm/prompts.py`.
- Промоція переможця = перенесення тексту в `EXTRACTION_SYSTEM`
  (єдине джерело правди для прод і eval — див. CLAUDE.md).
- Артефакт `extraction_outputs.json` фіксує шлях + sha256 промпта,
  яким зроблено прогін.

Дизайн: `docs/extraction-quality/2026-06-12-extraction-prompt-variant-design.md`
```

- [ ] **Step 2: Оновити `scripts/extraction/extraction_quality_eval.md`.** У таблицю "Вхід" додати рядок:

```markdown
| `--extraction-prompt` | — (продакшн-промпт) | шлях до файлу з альтернативним system prompt екстракції; ім'я+sha256 пишуться в метадані артефакта |
```

У розділ "Приклади запуску" додати:

```markdown
**9. A/B промпт-варіант екстракції** — кандидат із `scripts/data/prompts/`, окрема вихідна тека:
\`\`\`bash
.venv/bin/python scripts/extraction/extraction_quality_eval.py \
  --extraction-prompt scripts/data/prompts/extraction_v2_modality.md \
  --extractors gemini/gemini-3.1-flash-lite-preview \
  --output-dir scripts/outputs/extraction_eval_prompt_v2 \
  --gold-only
\`\`\`
```

- [ ] **Step 3: Commit**

```bash
git add scripts/data/prompts/README.md scripts/extraction/extraction_quality_eval.md
git commit -m "docs(extraction): тека промпт-варіантів + документація --extraction-prompt"
```

---

### Task 7: Фінальна перевірка

- [ ] **Step 1: Повний пакет тестів**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (231+ passed, без регресій)

- [ ] **Step 2: Lint**

Run: `.venv/bin/ruff check src/prophet_checker/analysis/extractor.py scripts/extraction/ tests/test_analysis_extractor.py tests/test_extraction_quality_eval.py`
Expected: `All checks passed!` (наявні давні помилки в інших файлах — не в скоупі)

- [ ] **Step 3: Жива перевірка без варіанта (дефолт = production)**

Run:
```bash
.venv/bin/python scripts/extraction/extraction_quality_eval.py \
  --stages 1 --limit 2 --no-gold \
  --extractors gemini/gemini-3.1-flash-lite-preview \
  --output-dir /tmp/prompt_variant_check_default
```
Expected: у виводі рядок `extraction prompt: production (<12 hex>)`; у `/tmp/prompt_variant_check_default/extraction_outputs.json` → `metadata.extraction_prompt == "production"`.

- [ ] **Step 4: Жива перевірка з варіантом**

Run:
```bash
printf 'You are a test prompt. Respond ONLY with raw JSON: {"predictions": []}\n' > /tmp/test_variant.md
.venv/bin/python scripts/extraction/extraction_quality_eval.py \
  --stages 1 --limit 2 --no-gold \
  --extraction-prompt /tmp/test_variant.md \
  --extractors gemini/gemini-3.1-flash-lite-preview \
  --output-dir /tmp/prompt_variant_check_variant
```
Expected: `extraction prompt: /tmp/test_variant.md (<інший hex>)`; в артефакті — шлях і хеш варіанта; claims порожні (тест-промпт наказує повертати порожньо — це і доводить, що варіант реально підставився).

- [ ] **Step 5: Якщо все зелене — готово.** Окремого коміту не треба (зміни закомічені в Tasks 1–6).
