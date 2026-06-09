# `extraction_quality_eval.py` — Extraction Quality Eval (LLM-as-judge)

3-стадійний LLM-as-judge eval **якості витягнутих прогнозів**: ганяє кілька extractor-моделей на тих самих постах, суддя (Opus) оцінює кожен claim, далі агрегуються метрики precision / recall / quality по моделях.

**Spec:** [`docs/extraction-quality-eval/2026-04-21-extraction-quality-eval-design.md`](../../docs/extraction-quality-eval/2026-04-21-extraction-quality-eval-design.md)

## Стадії

| Стадія | Що робить | Артефакт |
|--------|-----------|----------|
| 1 — extraction | кожна extractor-модель витягає claims з постів | `extraction_outputs.json` (модель → пост → claims[]) |
| 2 — judge | суддя оцінює кожен claim + репортить пропущене | `extraction_judgements.json` (per-claim вердикти + missed_predictions) |
| 3 — aggregate | зводить метрики по моделях | `extraction_eval_report.json` (агреговані метрики) |

Стадії можна запускати окремо (`--stages`), бо кожна читає артефакт попередньої з `--output-dir`.

## Вхід

| Аргумент | Дефолт | Опис |
|----------|--------|------|
| `--posts` | `scripts/data/sample_posts.json` | пул постів `[{id, person_name, published_at, text}]` |
| `--gold` | `scripts/data/gold_labels.json` | `[{id, has_prediction}]` — для recall та `--gold-only` |
| `--extractors` | Flash Lite, DeepSeek, Sonnet, Gemini 3 Flash | CSV моделей-екстракторів |
| `--judge` | `anthropic/claude-opus-4-6` | модель-суддя |
| `--author` | `Арестович` | фільтр постів за `person_name` |
| `--stages` | `1,2,3` | які стадії запускати (CSV) |
| `--limit` | — | обмежити к-сть постів (dry-run / дебаг) |
| `--gold-only` | off | лише пости з `gold_labels.json` (97 для Арестовича) |
| `--no-gold` | off | запуск **без** gold; `missed_rate`/`gold_agreement` → `null` |
| `--output-dir` | `scripts/outputs/extraction_eval/` | куди писати артефакти |

`--no-gold` і `--gold-only` — **взаємовиключні**. API-ключі провайдерів — у `.env` (`GEMINI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / …), бо скрипт ганяє LLM.

## Вихід

У `--output-dir`:
- `extraction_outputs.json` — Стадія 1.
- `extraction_judgements.json` — Стадія 2.
- `extraction_eval_report.json` — Стадія 3 (+ CLI-таблиця `avg_score / hall_rate / missed / claims`).

**Без gold (`--no-gold`):** judge-only метрики (`avg_quality_score`, `hallucination_rate`, `verdict_distribution`, `missed_predictions_count`) рахуються; `missed_rate` і `gold_agreement` = `null`. Recall і коректність true-negative без gold **не вимірюються** — лише якість того, що екстрактор реально видав.

## Приклади запуску

Усі команди — з кореня репо, через `.venv/bin/python`.

**1. Повний прогін з gold (дефолт)** — усі стадії, усі моделі, на gold-розмічених постах:
```bash
.venv/bin/python scripts/extraction/extraction_quality_eval.py --gold-only
```

**2. Без gold** — judge-only якість на довільних постах:
```bash
.venv/bin/python scripts/extraction/extraction_quality_eval.py --no-gold --limit 100
```

**3. Швидкий dry-run** — кілька постів, одна дешева модель:
```bash
.venv/bin/python scripts/extraction/extraction_quality_eval.py \
  --extractors gemini/gemini-3.1-flash-lite-preview --limit 5 --no-gold
```

**4. Лише агрегація** (перерахувати звіт із наявних judgements, без LLM):
```bash
.venv/bin/python scripts/extraction/extraction_quality_eval.py --stages 3
# те саме без gold:
.venv/bin/python scripts/extraction/extraction_quality_eval.py --stages 3 --no-gold
```

**5. Лише екстракція** (Стадія 1 — наповнити `extraction_outputs.json`):
```bash
.venv/bin/python scripts/extraction/extraction_quality_eval.py --stages 1 --gold-only
```

**6. Інкрементально додати модель** — витягти + засудити лише нову, змерджити з рештою:
```bash
.venv/bin/python scripts/extraction/extraction_quality_eval.py \
  --stages 1,2 --extractors openai/gpt-4o-mini --gold-only
.venv/bin/python scripts/extraction/extraction_quality_eval.py --stages 3 --gold-only
```

**7. Інший суддя / вихідна тека:**
```bash
.venv/bin/python scripts/extraction/extraction_quality_eval.py \
  --judge anthropic/claude-sonnet-4-6 \
  --output-dir scripts/outputs/extraction_eval_sonnet_judge --gold-only
```

**8. Явні вхідні / вихідні файли** — `--posts`/`--gold` (вхід) + `--output-dir` (вихід):
```bash
# свій пул постів + своя вихідна тека, без gold
.venv/bin/python scripts/extraction/extraction_quality_eval.py \
  --posts scripts/data/sample_posts_100.json \
  --output-dir scripts/outputs/eval_sample100 \
  --no-gold

# свої пости + свій gold + своя вихідна тека
.venv/bin/python scripts/extraction/extraction_quality_eval.py \
  --posts scripts/data/sample_posts.json \
  --gold scripts/data/gold_labels.json \
  --output-dir scripts/outputs/eval_custom \
  --gold-only
```
> **Вхід** задають `--posts` (пости) і `--gold` (gold-мітки) — будь-які шляхи до JSON потрібного формату. **Вихід** — `--output-dir`: туди пишуться **3 артефакти з фіксованими іменами** (`extraction_outputs.json`, `extraction_judgements.json`, `extraction_eval_report.json`); окремо їх не перейменувати, тож для паралельних прогонів задавай різні `--output-dir`.
