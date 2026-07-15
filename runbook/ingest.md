# Runbook — інжест даних (локально)

Наповнити локальну БД прогнозами, щоб `POST /answer`, `POST /query` і Telegram-бот
([`bot.md`](bot.md)) мали з чого відповідати. Порожній корпус → усе відповідає відмовою.

Два шляхи: **швидкий** (seed з готового `corpus.json` — для дева) і **реальний пайплайн**
(колектор → екстрактор, дорожчий). Для боксу/прода — [`first-ingest.md`](first-ingest.md).

## Передумови

- Postgres піднято: `docker compose up -d` (контейнер `prophet_postgres`).
- Міграції накатано: `.venv/bin/alembic upgrade head`.
- `OPENAI_API_KEY` у `.env` — потрібен на кроці embeddings.

## Швидкий шлях: seed з corpus.json

173 реальні прогнози Арестовича з `scripts/data/retrieval/corpus.json` — без колектора
й без LLM-екстракції. Обидва кроки ідемпотентні (повторний прогін нічого не дублює).

1. **Засіяти прогнози** — створює Person + RawDocument-заглушку і вантажить прогнози
   через продакшн-репозиторії:

   ```
   .venv/bin/python scripts/retrieval/seed_corpus_from_json.py
   ```

   Друкує `seeded N predictions` (N = скільки нових; на повторному прогоні — `0`).

2. **Добілити embeddings** — інакше векторний пошук прогнозів не бачить:

   ```
   .venv/bin/python scripts/ingestion/backfill_embeddings.py
   ```

   Ембедить `claim+situation` (`text-embedding-3-small`) для прогнозів без вектора; уже
   заембеджені пропускає (`Skipping already backfilled...`). Друкує `backfilled embeddings: N`.

## Перевірка

```
docker exec prophet_postgres psql -U prophet -d prophet_checker -tA \
  -c "select 'predictions='||count(*) from predictions;" \
  -c "select 'embedded='||count(*)  from predictions where embedding is not null;"
```

Успіх = `predictions > 0` **і** `embedded = predictions` (усі прогнози з вектором).
Після цього можна піднімати бота — [`bot.md`](bot.md).

## Реальний пайплайн (колектор → екстрактор)

Коли треба свіжі пости з Telegram, а не заморожений `corpus.json`. Дорожче: LLM-екстракція
на кожен пост + конект Telethon-колектора.

- **Смоук одного каналу** (1 пост, ~$0.001–0.005):

  ```
  .venv/bin/python scripts/ingestion/integration_smoke.py --channel @arestovich --limit 1
  ```

- **Повний цикл** — підняти `.venv/bin/python -m prophet_checker`, тоді:

  ```
  curl -X POST localhost:8000/ingest/run
  ```

  ⚠️ Цикл **без ліміту** — збирає всі пости від курсора `last_collected_at` (уся історія
  @arestovich ≈ 5572 пости = стільки ж LLM-викликів). Спершу постав джерелу вузьке вікно
  (див. [`first-ingest.md`](first-ingest.md), крок «засіяти Person + Source»).

- **На проді (боксі)** — те саме, але через SSH, однією командою:

  ```
  ./deploy/ingest.sh              # з підтвердженням; -y щоб пропустити
  ./deploy/ingest.sh --dry-run    # надрукувати план, нічого не робити
  ```

  Резолвить бокс → SSH → той самий `curl -X POST /ingest/run` **на боксі** (порт 8000 лише
  на localhost боксу) → чекає `CycleReport` і друкує підсумок. Той самий ⚠️ no-limit
  застосовний — вузьке вікно джерела став заздалегідь. Таймаут циклу — `--timeout <сек>`
  (дефолт 900). Побратими: `deploy.sh`, `logs.sh`, `status.sh`.

Передумови пайплайну: `TELEGRAM_API_ID/HASH` + `tg_session.session` (колектор),
`GEMINI_API_KEY` (екстракція).

## Пам'ятати

- **Verification** прогнозів (confirmed / refuted / ...) — окремий крок (`verification/`
  пакет); seed вантажить статуси як у `corpus.json`, наново їх не рахує.
- `backfill_embeddings` без `OPENAI_API_KEY` впаде — ключ обов'язковий.
- Гроші течуть лише на embeddings (seed безкоштовний) і на реальному пайплайні (LLM).

---

_Перевірено 2026-07-12 (локальна БД, гілка `docs/split-runbooks`):_

- _`seed_corpus_from_json.py` → `seeded 0 predictions` (ідемпотентно на наявних 173)._
- _`backfill_embeddings.py` → `backfilled embeddings: 0` (усі 173 вже з вектором)._
- _Перевірка: `predictions=173`, `embedded=173`._
