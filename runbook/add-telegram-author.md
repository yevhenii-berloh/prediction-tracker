# Runbook: додати нового автора (Telegram)

Завести ще одного публічного діяча в прод: створити рядки `persons` + `person_sources`,
зібрати його пости і переконатись, що бот відповідає про нього. Ендпоінта для джерел
нема — джерело існує лише як рядок у БД, тож засівання йде через SQL.

Це runbook для **другого і далі** автора на живій базі. Перший автор на порожньому
проді — інший сценарій (там ще й RDS cutover): [`first-ingest.md`](first-ingest.md).
Локальний дев-інжест — [`ingest.md`](ingest.md).

## Що міняється, а що ні

**Пайплайн author-agnostic — коду міняти не треба.** `run_cycle` бере всі джерела з
`enabled=true` (`list_active_sources`, `storage/postgres.py:180`), а планувальник запиту
тягне `persons.list_all()` і кладе імена авторів прямо в промпт
(`query/planner.py:28`, `build_self_query_prompt`). Новий рядок у БД → автор одразу
доступний і в інжесті, і в пошуку.

**Виняток — привітання бота.** `START_TEXT` у `src/prophet_checker/bot/texts.py:18`
каже «зараз у базі — прогнози Олексія Арестовича» і дає приклади питань про нього. Це
єдине місце з хардкодом імені; оновити після успішного інжесту (Крок 6).

## Передумови

- Середовище підняте: `./deploy/status.sh --no-ssh` → `UP`. Якщо `PAUSED` —
  `./deploy/start.sh` ([`stop-env.md`](stop-env.md)).
- Канал публічний, або акаунт із `tg_session` у нього доданий — інакше Telethon
  `get_entity` впаде (`sources/telegram.py:35`).
- **Ім'я автора — те, яким його спитає користувач** («Олексій Арестович»), бо саме
  `persons.name` бачить планувальник. Не хендл каналу.

## Крок 1 — перевірити канал локально

Один пост через справжні Telegram + LLM (~$0.001–0.005). Ловить друкарську помилку в
хендлі, приватний канал і протухлу сесію **до** того, як платити за бекфіл.

```bash
.venv/bin/python scripts/ingestion/integration_smoke.py --channel @newauthor --limit 1
```

## Крок 2 — засіяти Person + Source у прод

`deploy/psql.sh` сам тягне `.env` із секрет-бакета, резолвить бокс і піднімає SSH-тунель
до приватного RDS.

Вікно збору задає `last_collected_at` — курсор, від якого `TelegramSource` починає читати.
**Став вузьке вікно.** `POST /ingest/run` не приймає ліміт, тож курсор — єдиний важіль
вартості: кожен пост довший за 80 символів = один LLM-виклик.

```bash
./deploy/psql.sh -f - <<'SQL'
WITH p AS (
  INSERT INTO persons (id, name, description)
  VALUES (gen_random_uuid()::text, 'Ім''я Прізвище', 'Ukrainian public figure')
  RETURNING id
)
INSERT INTO person_sources (id, person_id, source_type, source_identifier, enabled, last_collected_at)
SELECT gen_random_uuid()::text, p.id, 'telegram', '@newauthor', true, now() - interval '3 days'
FROM p;
SQL
```

`description` — NOT NULL без server-default, тож у сирому SQL його треба вказати явно.

Перевірити, що джерело з'явилось:

```bash
./deploy/psql.sh -c "SELECT source_type, source_identifier, enabled, last_collected_at FROM person_sources;"
```

## Крок 3 (опційно) — звузити цикл до нового автора

`run_cycle` не вміє «лише це джерело» — він обходить **усі** `enabled=true`. Тому перший
прогін за новим автором заразом добере свіжі пости всіх старих. Якщо це небажано
(гроші, час, шум у звіті) — тимчасово вимкни інші:

```bash
./deploy/psql.sh -c "UPDATE person_sources SET enabled=false WHERE source_identifier <> '@newauthor';"
```

Повернути назад — Крок 6. **Не забудь**: поки вимкнено, прод не збирає нічого іншого.

## Крок 4 — запустити ingest

```bash
./deploy/ingest.sh                 # з підтвердженням
./deploy/ingest.sh --timeout 1800  # довший ліміт, якщо вікно широке
```

Прогін синхронний. Живі логи в іншому вікні: `./deploy/logs.sh`.

Очікуєш `CycleReport`, де серед `channels_processed` є звіт для нового `person_source_id`
з ненульовими `posts_seen` / `predictions_extracted`.

## Крок 5 — перевірити результат

```bash
./deploy/psql.sh --stats
```

Зріз друкує автора, `docs`, `predictions`, `verified` і курсор інжесту. Успіх = новий
автор у таблиці з `predictions > 0`.

Далі — верифікація прогнозів, це окремий крок: `./deploy/verify.sh`
([`verify.md`](verify.md)). Інжест лишає прогнози в статусі `unresolved`.

## Крок 6 — прибрати за собою

1. Якщо вимикав інші джерела на Кроці 3 — увімкнути назад:

   ```bash
   ./deploy/psql.sh -c "UPDATE person_sources SET enabled=true;"
   ```

2. Оновити `START_TEXT` у `src/prophet_checker/bot/texts.py` — перелік авторів і приклади
   питань. Без цього бот далі каже, що в базі лише Арестович. Деплой: `./deploy/deploy.sh`
   ([`deploy.md`](deploy.md), [`bot.md`](bot.md)).

## Розширити вікно потім

Курсор рухається вперед автоматично, тож наступні цикли беруть лише нові пости. Щоб
забрати глибшу історію — відмотай курсор назад і прожени інжест ще раз:

```bash
./deploy/psql.sh -c "UPDATE person_sources SET last_collected_at = now() - interval '30 days' WHERE source_identifier='@newauthor';"
```

Повний бекфіл — epoch, а **не** `NULL` (колонка NOT NULL):

```bash
./deploy/psql.sh -c "UPDATE person_sources SET last_collected_at = '1970-01-01' WHERE source_identifier='@newauthor';"
```

`TelegramSource` трактує курсор із роком ≤1970 як «без `offset_date`» і читає канал з
початку (`sources/telegram.py:36`). Це може бути тисячі постів — стільки ж LLM-викликів.

## Локально (dev)

На локальній БД засівання і прогін роблять одною командою — SQL не потрібен:

```bash
.venv/bin/python scripts/ingestion/run_ingestion.py --channel @newauthor --limit 20
```

Скрипт кличе `ensure_person_source` (ідемпотентний: рядок уже є → нічого не робить) і
одразу ганяє `run_cycle(limit=...)`. Тут `--limit` рятує від рахунку: курсор ставиться
на epoch, тобто без ліміту це **вся** історія каналу.

Для прода цей шлях не годиться: хелпер ставить `name = channel` (хендл, не людське
ім'я), а планувальник показує LLM саме `persons.name`.

## Схема (для довідки)

- `persons` — `id` (uuid-text), `name` (бачить планувальник запиту), `description` (NOT NULL).
- `person_sources` — `person_id` (FK), `source_type='telegram'`, `source_identifier='@channel'`
  (йде прямо в Telethon `get_entity`), `enabled` (фільтр `list_active_sources`),
  `last_collected_at` (курсор, NOT NULL).
- Потік: `run_cycle` → `list_active_sources` → `TelegramSource.collect(since=курсор)` →
  extractor → `raw_documents` + `predictions`; курсор рухається per-post на
  `published_at` обробленого поста.
