# Дизайн: вибір промпт-варіанта екстракції в extraction_quality_eval

**Дата:** 2026-06-12
**Статус:** затверджено (brainstorming-сесія)
**Трек:** extraction-quality

## Контекст

Eval на 100 постах Арестовича (суддя Opus 4.8, `scenarios/11-06-2026-evaluate-extraction/new/`) показав: 58% витягнутих claim'ів (36/62) — не прогнози (avg_quality_score 1.76/3). Таксономія провалів: нормативні заяви → ствердні прогнози; аналіз чужих намірів як прогноз автора; підсилення хеджів; риторичні питання як твердження.

Покращення йтиме через ітерації промпта (рішення: тільки промпт, Flash Lite, один виклик; пріоритет — точність, допустимо до ~5 пропусків/100 постів). Для ітерацій потрібен інструмент: **можливість вказати, який саме system prompt використовувати, аргументом `extraction_quality_eval.py`** — без правок продакшн-коду на кожен експеримент.

Напруга, яку розв'язує дизайн: CLAUDE.md вимагає, щоб eval міряв той самий промпт, що працює в проді (без форків). Але розробка промпта потребує тестування кандидатів *до* промоції. Рішення: дефолт завжди продакшн; кандидат — явний opt-in аргументом; промоція переможця = перенесення тексту в `EXTRACTION_SYSTEM`.

## Рішення

### 1. Промпт-варіанти — файли в `scripts/data/prompts/`

- Один файл = один system prompt, plain text/markdown (напр. `extraction_v2_modality.md`).
- Кандидати — eval-вхід, тому живуть у `scripts/data/` (конвенція репо), а не в `src/`.
- Зручно diff'ати між собою та з `EXTRACTION_SYSTEM`.
- **Без аргументу — продакшн-промпт із `prompts.py`** (інваріант "eval міряє прод" зберігається).

### 2. Прокидання варіанта в екстрактор

Три ланки, кожна — опціональний параметр з дефолтом "як зараз":

1. `PredictionExtractor(llm, system_prompt: str | None = None)` ([src/prophet_checker/analysis/extractor.py](../../src/prophet_checker/analysis/extractor.py)) — у `extract()`: `self._system_prompt or get_extraction_system()`. Продакшн-`factory.py` не змінюється.
2. `_default_extractor_factory(model_id, system_prompt: str | None = None)` ([scripts/extraction/detection_eval.py](../../scripts/extraction/detection_eval.py)) — прокидає в конструктор.
3. `extraction_quality_eval.py`: аргумент `--extraction-prompt <шлях>` (дефолт відсутній = продакшн). Stage 1 читає файл і передає текст через обгортку-замикання поверх `_default_extractor_factory`.

### 3. Простежуваність

- Метадані `extraction_outputs.json`: `extraction_prompt` (шлях або `"production"`) + `extraction_prompt_sha256` (перші 12 hex-символів sha256 тексту промпта — захист від непоміченої правки файлу між прогонами).
- Run plan друкує `extraction prompt: <ім'я> (<хеш>)` до першого API-виклику.
- Merge-логіка Stage 1 без змін: прогони різних промптів порівнюються через окремі `--output-dir`.

### 4. Тести

- `PredictionExtractor`: з override використовує переданий промпт; без — `get_extraction_system()` (мок LLM, перевірка kwarg `system=`).
- `_default_extractor_factory` прокидає параметр.
- CLI парсить `--extraction-prompt`; метадані з prompt-полями пишуться в артефакт.
- Жива перевірка: `--stages 1 --limit 2` з варіантом-файлом → ім'я/хеш видно в run plan і метаданих.

## Поза скоупом

- Текст нового промпта (підхід "R4-модальність + контрастні приклади") — окрема задача після цього інструмента.
- Методологія перетестування (dev-підмножина / повний набір / gold held-out) — окреме обговорення.
- Переекстракція наявних записів БД.
- `--extraction-prompt` для `detection_eval.py` CLI (фабрика підтримуватиме параметр, але CLI-плумбінг — лише в extraction_quality_eval).
