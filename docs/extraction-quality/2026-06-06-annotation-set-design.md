# Набір для ручної оцінки екстракції (annotation set) — Design

**Дата:** 2026-06-06
**Статус:** approved (очікує review spec)
**Трек:** extraction-quality

## Мета

Скрипт, що будує збалансований набір постів для **ручної оцінки** якості екстрактора: людина відкриває посилання, читає пост і виставляє оцінку + пояснення по кожному пості та кожному claim-у.

## Вимоги (від користувача)

1. N постів визначеної довжини (дефолт 100).
2. Баланс **50/50**: половина з знайденими передбаченнями, половина без.
3. Для кожного поста — **посилання** на повний текст (без вкладеного тексту).
4. Формат — **JSON**.
5. Для кожного поста/claim-а — порожні поля `score` + `note` для ручного заповнення.

## Прийняті рішення

- **Позитиви (з передбаченнями, 50)** — з **БД**: `raw_documents`, що мають предікшени, разом із готовими claim-ами. Безкоштовно, без LLM.
- **Негативи (без, 50)** — **точковий прогін екстрактора**: у БД негативів немає (ingestion персистить лише пости з предікшенами), тож ганяємо extractor на пулі з `all.json`, беремо пости з **0 claims**, поки не набереться 50. Це єдине джерело справжніх extractor-judged negatives.
- **Тільки посилання**, без вкладеного `text`. URL будуємо з `id` (формати `tg:@chan:20` і `chan_7780` → `https://t.me/chan/<msgid>`, без `@`-бага).
- **Тип оцінки вільний** — `score: null` + `note: ""`, шкалу визначає користувач при заповненні.

## Схема JSON

```json
{
  "meta": {"created":"YYYY-MM-DD","model":"gemini/gemini-3.1-flash-lite-preview","n":100,
           "with_predictions":50,"without_predictions":50,"seed":42,"min_chars":300},
  "posts": [
    {
      "post_id": "tg:@O_Arestovich_official:20",
      "url": "https://t.me/O_Arestovich_official/20",
      "published_at": "2020-03-13",
      "has_predictions": true,
      "source": "db",
      "post_score": null,
      "post_note": "",
      "claims": [
        {"claim_text":"...","situation":"...","prediction_date":"...",
         "target_date":"...","topic":"...",
         "claim_score": null, "claim_note": ""}
      ]
    },
    {
      "post_id": "O_Arestovich_official_7780",
      "url": "https://t.me/O_Arestovich_official/7780",
      "published_at": "...",
      "has_predictions": false,
      "source": "extractor_pool",
      "post_score": null,
      "post_note": "",
      "claims": []
    }
  ]
}
```

Поля для ручного заповнення (порожні): `post_score`, `post_note` на кожному пості; `claim_score`, `claim_note` на кожному claim-і.

## Компоненти

Один скрипт `scripts/extraction/build_annotation_set.py` (module-header docstring дозволений):

- `post_url(post_id) -> str` — будує t.me-лінк з обох форматів `id`, прибирає `@`.
- `load_db_positives(session_factory, n, min_chars, seed) -> list[dict]` — запит `raw_documents` з предікшенами (фільтр довжини), seed-вибірка n, мапінг у схему (`source="db"`, `has_predictions=true`, claims із таблиці predictions).
- `collect_extractor_negatives(posts, extractor, n, min_chars, seed, max_extractions, exclude_urls) -> list[dict]` — фільтр `all.json` (Арестович, `min_chars`, не серед позитивів), seed-перемішування, прогін extractor, збір постів із 0 claims до n або кепу.
- `build_extractor(model_id) -> PredictionExtractor` — як у `run_extraction.py` (LLMClient, temp=0).
- `main()` — args, виклик, злиття, запис JSON, підсумковий лог.

## Потік даних

1. Позитиви з БД (free) → 50.
2. Негативи: extractor на пулі `all.json` → 50 із 0 claims.
3. Злити (позитиви + негативи), записати JSON у `scripts/outputs/annotation/annotation_set.json`.

## Аргументи CLI

`--n 100` (ділиться 50/50), `--min-chars 300`, `--seed 42`, `--model gemini/gemini-3.1-flash-lite-preview`, `--max-extractions 300`, `--output <path>`.

## Edge cases

- **<50 позитивів у БД** → беремо скільки є, warn, `with_predictions` у meta = факт.
- **Пул негативів вичерпано / кеп досягнуто до 50** → warn, пишемо скільки набралось.
- **Помилка extractor на пості** → skip + log, не зупиняємось.
- **Непарний `--n`** → позитивів `n//2`, негативів решта.

## Доступ до інфри

- БД: `Settings().database_url` → `create_async_engine` + `async_sessionmaker` (як `run_ingestion.py`). Запит через ORM-моделі `RawDocumentDB`/`PredictionDB`.
- LLM: `LLMClient` (Gemini Flash Lite) для негативів.

## Поза скоупом

- Жодних змін у `prompts.py`/`extractor.py`/схемі БД.
- Автоматичне обчислення метрик — ні (це РУЧНА оцінка; набір лише готує дані).
- Завантаження тексту в JSON — ні (тільки посилання).
- Юніт-тести — мінімальні (чистий `post_url` вартий 1–2 тестів; решта — IO/LLM, валідується прогоном). Наявні 205 лишаються зеленими.

## Обмеження проєкту

NO inline comments; module-header docstring дозволений. `.venv/bin/python`. Українські коміти. Перевірка: `pytest tests/ -q` → 205+ passed; реальний прогін → валідний JSON 50/50.
