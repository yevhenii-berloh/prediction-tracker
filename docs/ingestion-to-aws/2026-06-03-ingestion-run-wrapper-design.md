# Ingestion-run wrapper + FK persistence fix — Design

**Дата:** 2026-06-03
**Статус:** Spec ready
**Контекст:** Щоб smoke-тестувати `VerificationOrchestrator` (Task 20) проти реальної БД,
потрібні unverified прогнози У Postgres. `integration_smoke.py` — це validation-smoke, не
інструмент наповнення. Треба окремий скрипт, що **запускає ingestion (Telegram→extract→persist)**
як чистий operational entrypoint — обгортка навколо `IngestionOrchestrator`.

---

## Знайдений баг (драйвер цього спеку)

`IngestionOrchestrator.run_cycle` витягує claims з `raw_doc` і зберігає **predictions** з
`document_id = raw_doc.id`, але **`save_document` ніде не викликається в runtime** (лише в тесті).
`predictions.document_id` має enforced FK → `raw_documents.id` (NOT NULL). Тобто перший же пост із
прогнозом → `prediction_repo.save()` падає на FK при commit. Не спливало, бо orchestrator-тести
ходять через `FakePredictionRepo` (без БД/FK). Реальний smoke це викриває.

**Рішення (узгоджено):** зберігати `raw_document` (provenance — джерело прогнозу: текст/дата/url,
traceability, RAG-evidence, re-extraction). Пост вже в пам'яті під час ingestion → зберегти майже
безкоштовно.

---

## Дизайн (Approach A)

Чотири частини: FK-фікс + `limit`-параметр (прибирає monkey-patch) + wrapper CLI + docs.

### Part 1 — FK persistence fix (production)

- `PostgresSourceRepository.save_document(doc, session=None)` — додати опційний `session=` (як уже
  мають `save`/`update_source_cursor`). У session-режимі — `session.merge(db_obj)` (ідемпотентно,
  без commit; re-run не PK-клешить). Без session — власна сесія + commit (як зараз).
- `IngestionOrchestrator.run_cycle` — у persist-транзакції зберігати `raw_doc` через
  `source_repo.save_document(raw_doc, session=session)` **перед** `prediction_repo.save(...)`,
  **лише** у гілці з прогнозами. Пост без прогнозів нічого не FK-реферує, а re-collection і так
  гейтиться cursor'ом — зберігати його немає сенсу (тільки bloat).
- `FakeSourceRepo.save_document(doc, session=None)` — узгодити сигнатуру (приймати/ігнорувати session).

### Part 2 — `limit` як first-class параметр (прибирає monkey-patch)

- `Source` Protocol (`sources/base.py`): `collect(person_source, since=None, limit=None)`.
- `TelegramSource.collect` — приймає `limit`; зупиняється після N документів.
- `MockSource.collect` — приймає `limit` (для тестів).
- `IngestionOrchestrator.run_cycle(limit=None)` — прокидає `limit` у `source.collect(...)`.
- `integration_smoke.py` — прибрати `_patch_telegram_with_limit`, передавати `run_cycle(limit=…)`
  (DRY; усуває дубльований hack).

### Part 3 — wrapper CLI `scripts/run_ingestion.py`

- Module-header docstring (operational скрипт).
- Args: `--channel @arestovich` (обов'язково), `--limit N` (default — розумний, напр. 20).
- Кроки: `ensure_person_source(session_factory, channel)` → `build_orchestrator(settings, stack)` →
  `run_cycle(limit=args.limit)` → надрукувати `CycleReport` (posts_seen / posts_with_predictions /
  predictions_extracted / per-channel errors).
- `ensure_person_source` — **спільний** ідемпотентний helper (Person + active PersonSource для
  каналу, якщо ще нема), винесений з `integration_smoke._ensure_smoke_person_source` у спільний
  модуль `scripts/_ingestion_setup.py`, щоб обидва скрипти його ділили.

### Part 4 — docs

- Оновити `docs/verification-track/20-verification-orchestrator/real-db-smoke.md` Step 1:
  Варіант A → `scripts/run_ingestion.py --channel @arestovich --limit 10` замість `integration_smoke.py`.

---

## Компоненти та файли

| File | Зміна |
|---|---|
| `src/prophet_checker/storage/postgres.py` | `save_document` приймає `session=`, merge-ідемпотентність |
| `src/prophet_checker/storage/interfaces.py` | `SourceRepository.save_document` + `session=`; `Source.collect` + `limit=` |
| `src/prophet_checker/sources/base.py` | `Source.collect(..., limit=None)` (Protocol) |
| `src/prophet_checker/sources/telegram.py` | `collect` приймає `limit`, break після N |
| `src/prophet_checker/sources/mock.py` | `collect` приймає `limit` |
| `src/prophet_checker/ingestion/orchestrator.py` | save_document(raw_doc, session=) перед predictions; `run_cycle(limit=None)` → collect |
| `scripts/_ingestion_setup.py` | **новий** — `ensure_person_source` (спільний) |
| `scripts/run_ingestion.py` | **новий** — wrapper CLI |
| `scripts/integration_smoke.py` | прибрати monkey-patch, юзати limit-параметр + спільний ensure |
| `tests/test_ingestion_orchestrator.py` | run_cycle зберігає raw_document; threads limit |
| `tests/test_storage_postgres.py` | save_document(session) приєднується до сесії (merge) |
| `tests/test_sources_*` / mock | collect honors limit |
| `docs/.../real-db-smoke.md` | Step 1 → run_ingestion.py |

## Потік даних

`run_ingestion.py` → ensure_person_source → `build_orchestrator` → `run_cycle(limit)`:
для кожного active person_source → `source.collect(ps, since=cursor, limit)` → per post:
`extract` → (embed) → транзакція **{ save_document(raw_doc) ; save(predictions) ; advance cursor }**
→ `CycleReport`.

## Обробка помилок

- `run_cycle` уже ловить per-channel виняток у `ChannelReport.error` (зберігається).
- FK тепер задоволений (raw_doc у тій самій транзакції перед predictions) — атомарно: якщо save
  падає, відкочується весь пост (raw_doc + predictions разом).
- merge у save_document → ідемпотентність на re-run (cursor і так гейтить, merge — підстраховка).

## Тестування

- `tests/test_ingestion_orchestrator.py`: run_cycle викликає `save_document(raw_doc)` (FakeSourceRepo
  фіксує документ) до/разом із predictions; `run_cycle(limit=1)` з MockSource → збирає ≤1 пост.
- `tests/test_storage_postgres.py`: `save_document(doc, session=mock)` додає у передану сесію
  (через merge), без власного commit.
- `tests/` для sources: `MockSource.collect(..., limit=N)` віддає ≤N.
- Wrapper `run_ingestion.py`: без автотесту (потребує реального Telegram+DB+embeddings) —
  перевіряється `--help` + real-DB smoke.

## Залежності для запуску wrapper (real ingestion path)

`.env`: Telegram creds + `tg_session*` (collect), `LLM_*` (extractor — ідеально gemini flash lite),
`OPENAI_API_KEY` (embeddings — `build_orchestrator` будує `EmbeddingClient`), Postgres. Це повний
ingestion-стек; runbook це зафіксує.

## Поза скоупом

- Зміни схеми БД (FK уже існує; фіксимо лише persist-логіку).
- Відмова від embeddings у ingestion (orchestrator ембедить — лишаємо як є).
- Decoupled collect/extract pipeline (`get_unprocessed_documents`/`processed`) — окремий рефактор.
