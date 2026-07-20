# Prediction Tracker — Progress Log

Living log: time, cost, deliverables. Оновлюється коли завершується milestone або значуща задача.
Project-wide джерело правди по статусу; per-track деталі — у `docs/<track>/README.md`.

**Останнє оновлення:** 2026-07-20

---

## Current state (snapshot 2026-06-29)

| Metric | Value |
|--------|-------|
| Календарний час від старту | ~82 днів (2026-04-08 → 2026-06-29) |
| Commits | 344 (push відновлено, синхронізовано з origin) |
| Tests passing | 312 |
| Tasks completed | M1 (5/5) + M2 (5/5) + M2.5 (eval/data) + Ingestion→production track + Verifier-v2 track (19.5→19.9 + Task 20) + RAG-трек (retrieval → query → generation → eval v2 + answer-contract) |
| Tasks in flight | — |
| Tasks queued | Recheck-луп, автоматизація/розклад інжесту, GitHub Actions CI |
| AWS cost | розгорнуто й працює (EC2 t3.small + RDS db.t4g.micro); сума не відстежується — Cost Explorer на акаунті вимкнено |

**Активний фокус:** RAG-трек завершено по коду й зведено в `main`: retrieval → query → generation
(`POST /answer`) → **generation-eval v2** (ізольована генерація на **заморожених** gold-прогнозах;
faithfulness+completeness) + **RAG answer-contract** (відповідь прогноз→вердикт простою мовою, без
службових полів). Перший eval-прогін показав, що faithfulness 0.60 був ~90% **інструментальним
артефактом** (суддя не бачив date/confidence, які бачив генератор) → фікс: спільний `render_predictions`
для генератора й судді → **підтверджено повним прогоном (92 кейси): faithfulness 0.60→0.947,
recall 1.0, стиль чистий**. **Наступне:** κ-калібрування судді (вкл. трактування `status` як
авторитету для вердикту — див. нотатку нижче). Park: end-to-end RAG-eval + поріг релевантності
(чип `task_a358c756`), recheck-луп verifier.

**Оновлення 2026-07-18:** деплой більше не queued — система відпрацювала **повний прод-цикл** на AWS:
інжест 2026-07-15 (2238 документів, канал догнано) → верифікація 2026-07-16 (4116/4116, `failed=0`).
Деталі й застереження — у Notes. Незакрите на проді: автоматизація/розклад інжесту (запускається вручну).

> **Нумерація:** verifier-v2 track має власну внутрішню нумерацію (19.5/19.7/19.8/19.9/20).
> Її "Task 20" (orchestrator) — це verifier-track задача, не плутати з ранньою backlog-задачею
> "GitHub Actions CI".

---

## Phase 0–2: Foundation + AI Pipeline (Tasks 0–9) ✅ COMPLETE

- **M1 (0–4):** scaffold, config, Pydantic domain models, Protocol storage, SQLAlchemy ORM + Alembic. ~16 tests.
- **M2 (5–9):** Postgres storage impl, LiteLLM client, prompt templates, PredictionExtractor, PredictionVerifier v1 (згодом superseded Verifier v2). ~23 tests.

**Key decisions:** Python/FastAPI, monolith ports-and-adapters, Protocol storage + fakes, LiteLLM, PostgreSQL + pgvector.

---

## Phase 3 / 3.5: Eval, Data & Design Refresh ✅ COMPLETE

- **Збір даних:** 5572 Arestovich posts (Telethon), 130 gold detection labels.
- **Task 13 — Detection eval:** 5 моделей × 2 prompts → **Winner: Gemini 3.1 Flash Lite** (F1=0.848).
- **Task 13.5 — Extraction quality eval:** 3-stage LLM-as-judge → **production: Flash Lite** (33× дешевше, кращий recall). Деталі: [`docs/extraction-quality-eval/`](docs/extraction-quality-eval/).
- **Design refresh:** [`docs/architecture/2026-04-26-architecture-current.md`](docs/architecture/2026-04-26-architecture-current.md) (index + 7 flow docs), Verifier-v2 spec у [`docs/verifier-v2/`](docs/verifier-v2/).
- **Ключова знахідка:** 70–90% extracted claims мають `target_date=null` → блокує v1 verifier → драйвер Verifier v2.

---

## Phase 4: Ingestion → production ✅ COMPLETE

Ingestion pipeline + FastAPI HTTP trigger працюють end-to-end (підтверджено CLAUDE.md +
[`docs/architecture/2026-04-26-architecture-current.md`](docs/architecture/2026-04-26-architecture-current.md)).

| Task | Deliverable |
|------|-------------|
| 21 — TelegramSource adapter | Telethon `Source`, oldest-first для cursor-monotonic advance |
| 15 — IngestionOrchestrator | `run_cycle()` → collect → extract → persist → `CycleReport` |
| 16 — FastAPI app entry | `GET /health` + `POST /ingest/run`, composition root у `factory.py` |
| 17 — Docker Compose | Postgres + pgvector контейнер, local dev workflow |
| 18 — Alembic on real Postgres | міграції застосовуються на реальній БД |
| 19 — Integration smoke | `scripts/ingestion/integration_smoke.py` (real Postgres + Telegram + LLM) |

Специфікації: [`docs/ingestion-to-aws/`](docs/ingestion-to-aws/).

---

## Phase 5: Verifier-v2 track 🟢 MOSTLY COMPLETE

Повний статус + mermaid: [`docs/verification-track/README.md`](docs/verification-track/README.md).

| Sub-task | Статус | Результат |
|----------|--------|-----------|
| 19.5 — V2 schema + prompts + parser | ✅ | 4-status (confirmed/refuted/unresolved/premature) + strength + 6 urgency-полів |
| PredictionValue extension | ✅ | 8-й output (importance/resonance) |
| 19.7a — Gold v1 | ✅ | 35 Arestovich predictions, V2 schema |
| 19.8a–d — context→situation | ✅ | `situation` (model-paraphrase, presence-validated) замінив verbatim context |
| 19.8b — fresh gold | ✅ | 32 claims з situation (`scripts/data/verification_gold_labels.json`) |
| 19.7b — model eval | ✅ | 9 моделей × 32 gold → **production model = Gemini Flash Lite**. Сага тюнінгу V2→V7 + split: [`docs/verification-track/19-7b-verification-eval/prompt-history.md`](docs/verification-track/19-7b-verification-eval/prompt-history.md) |
| 19.9 — Split Verifier (2-call) | ✅ | verdict + assessment виклики розривають single-call tradeoff. **Flash Lite: firm-status 0.833 / strength 0.719 / value 0.812.** `Verifier` у `analysis/verifier.py`. Commits `de6afd4`→`a670158` |
| 20 — VerificationOrchestrator (first-pass) | ✅ | Pull get_unverified → `Verifier` → write-back з urgency-полями. `verification/` пакет + PREMATURE + update() V2 + factory + CLI. Commits `a2933a0`→`d329408`, 190→198 tests |

**Допоміжне:** 3-стадійний pipeline `extraction/sample_posts → extraction/run_extraction → verification/run_verification`
для ручного рев'ю якості (outputs у `scripts/outputs/pipeline_run/`).

---

## Phase 6: AWS deploy + CI 🟢 РОЗГОРНУТО (CI лишається)

| Task | Статус |
|------|--------|
| 23 — AWS RDS PostgreSQL + pgvector | ✅ живе — інстанс `prophet-data-dbinstance-*` `available`, прод-дані на ньому (перевірено 2026-07-18) |
| 24 — AWS EC2 + Docker deploy | ✅ живе — бокс `i-0b2811b60cb09de92` `running`, застосунок відповідає |
| 20 (master-plan) — GitHub Actions CI | 📋 |

Деплой не просто «реалізовано в коді» — він **працює на проді**: три CloudFormation-стеки (`secrets` + `compute` + `data`), EC2 t3.small з Docker Compose, дані на RDS PostgreSQL з pgvector, TLS-конект (`rds.force_ssl=1`), секрети з приватного S3 через IAM, доступ SSH-only. Повний цикл по корпусу пройшов на цій інфрі: інжест 2026-07-15, верифікація 2026-07-16 (див. Notes). Лишається 📋: автоматизація/розклад інжесту і GitHub Actions CI. Деталі: [`docs/aws-deploy/`](docs/aws-deploy/).

⚠️ **Перед будь-яким `update-stack` на `prophet-compute` запінити AMI** — `LatestAmiId` резолвиться на найновіший AL2023, а живий бокс на старішому, тож апдейт пересоздасть інстанс і вб'є бокс.

---

## Phase 7: Future (post-MVP)

1. **Verifier recheck-луп** — повторна перевірка `premature` за `next_check_at` до `max_horizon` (urgency-поля вже пишуться у Task 20).
2. Detection prefilter (`PredictionDetector`) — якщо two-tier.
3. ~~Telegram bot frontend~~ → зроблено (2026-07-11, `docs/telegram-bot/`); RAG query endpoint був готовий раніше.
4. News collector (Task 22) — для verifier evidence.
5. Continuous eval-loop (production quality monitoring).

- **RAG-цитати — посилання на пости у відповіді бота (2026-07-18).** Остання миля довіри: відповідь бота досі неможливо було перевірити, хоча джерела лежали в `AnswerResult.sources` і бот їх викидав. Тепер модель цитує **ідентифікатором** прогнозу, `resolve` (чиста) міняє їх на `[1]`, `[2]` за першою появою в тексті, `materialize` тягне документи й групує **одна цитата = один пост** (на проді 4116 прогнозів з 2238 постів, тож збіг документів — норма), бот шле блок «Джерела» з датою поста в HTML. Дизайн+план: [`docs/citations/`](docs/citations/). 13 задач TDD, сюїта 467.

  **Ключове рішення — цитувати UUID, а не порядковий номер**, і `render_predictions` лишити незмінним. З номером помилка на один символ дає **валідний** маркер на інший пост: посилання бреше, а рантайм цього не бачить. З ідентифікатором та сама помилка не резолвиться ні в що й падає голосно. Побічно це зберегло faithfulness-базлайн порівнюваним, бо суддя бачить ті самі джерела.

  **Eval — два scorer-и в наявному прогоні generation-eval**, не окремий трек: одна генерація замість двох, метрики зняті з тих самих відповідей (без чого пара completeness ↔ coverage безглузда). Precision судить **входження маркера** (одиницю окреслила сама модель), coverage детермінований. Faithfulness-судді подається текст **без** маркерів — інакше він рахував би `[1]` за частину твердження.

  **Результат (92 кейси):** **citation_precision 0.991, coverage 1.000**, faithfulness 0.993, 0 помилок. Пороги (0.95 / 0.90) взято.

  **Але перший прогін дав precision 0.801** — і це знову виявився **артефакт вимірювання, четвертий поспіль у цьому проєкті**. Провал концентрувався на негативному вердикті: 34 з 35 забракованих вердиктних маркерів — фраза «не справдився». Суддя відповідав не на те питання: citation precision має питати «чи з цього джерела взято це речення», а він питав «чи доводить джерело цей вердикт» — для RAG-джерела нерозвʼязне за побудовою, бо доказ провалу знає верифікатор, а не текст прогнозу. Промпт переписано: `status` оголошено достатнім доказом результату **в обох напрямках поіменно**, вимога зовнішнього підтвердження прямо заборонена. Змінився **лише промпт судді** — 0.801 → 0.991.

  **Залишкові 2 браки з ~189 маркерів розібрані, хибної атрибуції серед них нема:** один — обмеження одиниці вимірювання (речення з **двома** маркерами судиться проти одного джерела, тож джерело слушно не покриває другу половину); другий — плутанина судді на подвійному запереченні (негативний прогноз + `status=refuted`). Тобто 0.991 — консервативна нижня межа, як свого часу 0.947.

  **Уроки процесу, дорожчі за самі числа.** (1) Перший прогін довелось діагностувати навпомацки, бо `ScoreCard` для цитат не писав `detail` — виправлено, тепер пишуться маркер, id, речення, вердикт і причина. (2) Другий прогін **упав на 81/92** через `JSONDecodeError`: суддя додав прозу після JSON. Полагоджено `raw_decode`-ом у спільному хелпері, але ціна була весь прогін — виняток у scorer-і не ізольований і валить `run_eval` цілком, як збій ембедингу валить канал інжесту. Заведено окремо (чип `task_4fbc7122`).

  **Прапорець `citations_enabled` лишається `false` на проді** — вмикати за рішенням користувача.

### Tech debt / code-quality (parked 2026-06-30)

- **Ruff debt — 68 pre-existing errors на всьому дереві.** `ruff check .` червоний (хоча змінювані файли тримаються зеленими, і ruff НЕ в pre-commit, щоб не блокувати коміти). Розклад: 37×E402 (import не на початку — eval-скрипти з `sys.path`-бутстрапом без `# noqa: E402`), 19×F401 (невикористані імпорти), 5×F541 (f-string без плейсхолдера), 5×F811 (повторне визначення, у тестах), 2×E712 (`== True/False`). Концентрація: `tests/` (32), `scripts/extraction` (16), `scripts/verification` (11), `scripts/ingestion` (5), `src/.../storage` (4). Фікс: `ruff check . --fix` прибирає ~29 авто; решту E402 закрити `# noqa: E402` за патерном `scripts/retrieval/retrieval_eval.py`.
- **Cognitive-complexity grandfathered — 3 функції > порога 12.** Gate (complexipy ratchet, `[tool.complexipy] max-complexity-allowed=12`) пропускає наявні, блокує нові/гірші. Зарефакторити до ≤12 окремими TDD-кроками (поведінка незмінна, звіряти `complexipy src`): `IngestionOrchestrator::_process_channel`=19 (`ingestion/orchestrator.py` — найбільша робота), `PredictionExtractor::extract`=15 (`analysis/extractor.py`), `parse_verification_response_v2`=13 (`llm/prompts.py`).
- **Embedder-помилка → сирий HTTP 500 замість м'якої відмови (parked 2026-07-11, чип `task_3ddadfdd`).** `QueryOrchestrator.search` кличе `self._embedder.embed(...)` без обгортки; будь-яка помилка провайдера (OpenAI `RateLimitError`/`insufficient_quota`, 5xx) летить до `app.py`, де загальний `except Exception` віддає 500 з назвою типу винятку. Виявлено наживо під час hybrid-retrieval смоуку (вичерпана OpenAI-квота → `/answer` на запити 1-3 = 500). **Pre-existing** — не hybrid-retrieval внесла (`search` кликав `embed` без обгортки й до фічі; планер додано *перед* ембедом). **Чому варте фіксу:** це інфра/транзитний збій (retryable), не поганий запит — інший клас, ніж свідомий fail-fast `QueryPlanningError` (Р4); має віддавати **503 / «тимчасово недоступно»**, не 500. **Напрямок:** ловити провайдер-помилки ембедера на межі; перевірити, чи `EmbeddingClient` (`llm/embedding.py`) успадковує `num_retries=3`, як LLM-виклики. Обсяг: невеликий hardening + тест. Не терміново.
- **Збій ембедингу одного прогнозу валить увесь цикл каналу (parked 2026-07-15).** `IngestionOrchestrator._process_channel` ембедить прогнози в циклі всередині широкого `try/except`. Будь-який виняток `embed()` (провайдерська 5xx/квота або патологічний вхід) летить у цей `except` → `report.error = "halted at step=processing"` → **канал зупиняється на цьому пості**, решта постів не обробляється. Наживо: інжест-ран `4609c47b` спіткнувся на вході >8192 токени (`seen=1918`, halted). **Частину 1 вже полагоджено** (коміт `ff3574d`, `fix(embedding)`): `embed()` обрізає вхід до 8191 токена, тож найчастіший тригер — завеликий текст — знято. **Лишається (Частина 2):** окремий збій ембедингу не має валити канал. **Напрямок:** винести embed-цикл в окремий метод `_embed_predictions(predictions)` з per-prediction `try/except` — збій логувати `WARNING`, лишати `embedding=None` (колонка nullable — прогноз усе одно зберігається), цикл продовжувати. Метод **окремий навмисне:** `_process_channel` уже 19 cognitive-complexity (grandfathered, див. пункт вище) — інлайн-`try/except` підняв би її й завалив ratchet, а method extraction для метрики безкоштовний. Сестра-баг того самого класу в query-шляху — `task_3ddadfdd` вище. Обсяг: невеликий, TDD (mini-spec + test-list узгоджено в сесії). Не терміново, але це прод-robustness.

- **`prediction_date` може приїхати з тексту поста, а не з його дати (помічено 2026-07-18, чип `task_1b4e0d90`).** У замороженому gold є прогноз із `prediction_date = 1982-11-10` — дата смерті Брежнєва, очевидно згадана в тексті — при тому, що сам пост значно новіший. Знайдено проксі-методом: якщо відсортувати прогнози за номером телеграм-повідомлення (з `document_id` виду `tg:@channel:<msg_id>`), дати мають іти не спадаючи; на 95 прогнозів вийшла рівно одна інверсія, і саме ця. Проксі ловить лише стрибки **назад** відносно сусіда, тож 1 з 95 — **нижня межа** розбіжності, не справжня частка. Корінь у `analysis/extractor.py:72`: фолбек на дату поста спрацьовує, лише коли модель не повернула нічого або сміття; впевнено повернуту дату з тексту приймають як є, а промпт її саме й просить («when the prediction was made»). **Чому це більше за косметику:** hybrid retrieval фільтрує по `prediction_date` (запит «що казав у 2022» тихо промахується повз такі рядки), а верифікатор рахує від нього горизонти й `next_check_at`. **Напрямок:** обмежити видобуту дату датою документа — приймати, лише якщо вона не раніша за пост понад розумний проміжок, інакше фолбек і `WARNING`; далі зміряти справжню частку join-ом `predictions` × `raw_documents` на проді. Виявлено побіжно при дизайні RAG-цитат; у той скоуп не входить.

- **`raw_documents.processed = 0` на всьому проді (помічено 2026-07-18, НЕ досліджено).** Зріз `deploy/psql.sh --stats` показує 2238 документів і `processed = 0` — жодного. При цьому з тих самих документів видобуто 4116 прогнозів, тобто екстракція по них точно відпрацювала. Одне з двох: або прапорець мертвий (ніде не виставляється, а колонка лишилась від ранньої ітерації), або write-back інжесту його не оновлює. **Чому це варте уваги:** якщо якийсь шлях колись стане фільтрувати по `processed`, він побачить увесь корпус як необроблений і перемеле його вдруге — тихо й за гроші LLM. Поки що жодних наслідків не спостережено. **Напрямок:** знайти всі місця запису `processed` (grep по `storage/` + `ingestion/`), звірити з наміром у моделі `RawDocument`; якщо поле нікому не потрібне — прибрати міграцією, якщо потрібне — виставляти. Спершу діагностика, не фікс.

### Дослідження — hybrid structured+unstructured RAG search (parked 2026-07-04)

**Статус:** дослідження проведено; оформлено як Brain-вікі `wiki/concepts/hybrid-rag-search.md` (**2026-07-10**, 11 findings, цитовано). Дистильовані висновки лишаю тут як швидкий довідник (сирий звіт був у ефемерному воркфлоу-транскрипті; повний відновлено з журналу воркфлоу). **Впровадження — Частину B v1 реалізовано 2026-07-11** (self-querying + typed фільтри автор/дати); деталі нижче в Notes і в `docs/hybrid-retrieval/`.

**Проблема:** ретрив на чистому embedding погано працює для нашого кейсу — (1) імʼя автора відсутнє в тексті прогнозу, (2) дата прогнозу й дата прогнозованої події відсутні, (3) embedding слабко розрізняє роки (2022 vs 2023), (4) слабко матчить власні назви. Повʼязано з запаркованим порогом релевантності в retrieval (чип `task_a358c756`).

**Висновки (deep-research 2026-07-04, 105 агентів, adversarial-verified):**
- **Ядро:** виносити `author` / `prediction_date` / `forecast_event_date` у **structured-колонки** й фільтрувати SQL-предикатами поряд із vector-пошуком — не покладатися на embedding. Multi-Meta-RAG: додавання metadata-фільтра підняло Hits@4 0.663→0.792.
- **Query-understanding стадія:** вторинний LLM парсить NL-запит у структурований метадата-фільтр (self-querying / text-to-metadata-filter). Схему фільтрованих полів (імʼя/опис/тип) декларувати наперед.
- **Час:** моделювати дату-коли-сказано окремо від дати-прогнозованої-події (temporal IR: «focus time» ≠ timestamp). Типізовані DateTime-фільтри (`>`,`<`,`between`) обходять слабке розрізнення років у embedding.
- **Власні назви:** поєднати dense-вектор із лексичним/keyword (BM25) або entity-retrieval — embedding програє на entity-дискримінації.
- **pgvector-специфіка:** HNSW/IVFFlat застосовують `WHERE` **після** index-scan (post-filter → overfiltering, втрата recall); pgvector 0.8.0+ має iterative index scans + кращий планувальник; для дуже селективних предикатів (один автор) exact B-tree (100% recall) може бути кращим за ANN.

---

## Cost log (approximate)

| Категорія | Cost |
|-----------|------|
| LLM API (eval runs: detection + extraction quality + verification + prompt-tuning сага) | ~$25–35 |
| Claude Code dev (numerous Opus sessions) | ~$50–250 (estimated) |
| AWS | $0 |
| GitHub | $0 (public) |
| **Total to date** | **~$75–285** |

---

## Notes

- **Velocity** ~199 commits / ~56 днів ≈ 3.5 commits/день calendar; pet-project pace.
- **Pivot:** після ingestion→production завершився, фокус перейшов на Verifier v2 (раніше deferred) — він виявився найбільш ітерованою областю продукту.
- **Детермінований eval-інсайт (19.9):** temperature=0 для Flash Lite повністю детермінований → prompt-тюнінг ведеться як точна наука, без sampling-noise. Single-call має інхерентний tradeoff (strength-fix псує status); декомпозиція на 2 виклики його розриває.
- **RAG retrieval v1 (2026-06-21):** retrieval eval-харнес готовий (`scripts/retrieval/`, Tasks 1–10, 27 тестів; design+plan у `docs/retrieval-eval/`). **РІШЕННЯ:** робоча конфігурація retrieval зафіксована вольовим вибором — embedding = `text-embedding-3-small`, репрезентація = **`claim+situation`** (1536-dim = поточна колонка `predictions.embedding`, без міграції). Мультимодельне порівняння (MMTEB-screening + sweep) **PARKED** — харнес лишається для майбутнього прогону, не загублено. Прокинуто в прод-інжест (`embedding_text()` у `analysis/`, оркестратор ембедить claim+situation) + `embeddings_enabled=True` + backfill-скрипт.
- **RAG query serving (2026-06-22):** `POST /query` готовий end-to-end. `QueryOrchestrator.search` (embed → `search_similar`(scored) → `get_by_ids` → `QueryResult`), **retrieval-only (gen-ready)**, top-k + `distance` без порога. Нові доменні моделі `VectorMatch`/`RetrievedPrediction`/`QueryResult`; `get_by_ids` (order-preserving); endpoint + lifespan-wiring. Design+plan: [`docs/query-serving/`](docs/query-serving/). **Фікс:** `search_similar` фільтрує `embedding IS NULL` (інакше `cosine_distance(NULL)`→`distance=None`→краш на не-backfill'нутому корпусі). Backfill ідемпотентний (`is_embedding_present` → skip-already-embedded). Уся сюїта **270 тестів**. **Наступне:** прогнати backfill на проді (наразі всі 4046 прогнозів `embedding IS NULL`) + smoke `/query`; далі **v1.5 генерація** (`answer(QueryResult)` + citation/refusal/faithfulness-eval) і Telegram-бот.
- **RAG generation v1.5 (2026-06-25):** `POST /answer` готовий end-to-end. Окремий `AnswerOrchestrator(query_orchestrator, llm)` переюзає `QueryOrchestrator.search` → **short-circuit refusal на порожніх sources** (`REFUSAL_NO_DATA`, без виклику LLM) → інакше `build_rag_prompt` + `LLMClient.complete(RAG_SYSTEM)` → `AnswerResult{query, answer, sources}`. `build_rag_prompt` загартовано з magic-dict на типізований `list[RetrievedPrediction]` (id/дати/статус у контекст для цитування). LLM: Gemini 3.1 Flash Lite, `temperature=0`. Design+plan: [`docs/generation/`](docs/generation/). 4 коміти TDD (`d1ddfc2`→`75585ad`), уся сюїта **275 тестів**. **Наступне:** прогнати backfill + ручний smoke `/answer` на проді; далі **eval генерації** (faithfulness/citation/refusal), маркерні цитати [n]→id, поріг релевантності, Telegram-бот.
- **Eval framework `eval_common` (2026-06-27):** узагальнений eval-каркас `scripts/eval_common/` — конвеєр **dataset→runner→scorer→reporter**. Рішення (підкріплене deep-research, `docs/generation/2026-06-25-eval-research-summary.md`, 23/25 claims verified): **будувати тонкий власний, не adopt-ити Ragas/DeepEval** (вони — самі LLM-judge/NLI калькулятори; цінність = визначення метрик + структура, без важкої залежності). Узагальнений по `input`/`labels`/`result`/`Metrics` через `SerializeAsAny[BaseModel]` (інакше Pydantic губить поля сабкласу в JSON). `run_eval()` — тонкий оркестратор; `Judge`/`Scorer` Protocol-и + judge-гігієна (temp0, fingerprint, shuffle-опцій); `run_cases` з ізоляцією помилок. 8 задач TDD + **двостадійне рев'ю** (spec+quality субагентами; код-quality виявив реальний gap у `parse_model_id`). Мапінг 4 наявних евалів довів узагальненість (не RAG-специфічний — scorer↔aggregator вага зміщується). Design+plan: [`docs/eval-framework/`](docs/eval-framework/). +14 тестів.
- **Generation eval v1 (2026-06-27):** перший консумер `eval_common` — оцінка `POST /answer`. **3 метрики:** faithfulness (supported/total claims, decompose+entail одним judge-викликом), refusal correctness (answerable vs off-corpus, fast-path `REFUSAL_NO_DATA` + judge yes/no), **completeness/recall** (covered/expected sources — закрив сліпу зону precision-only: cherry-pick одного джерела давав «ідеальний» faithfulness; виявлено в рев'ю когерентності). Суддя — **крос-родинний Claude** (`anthropic/claude-opus-4-8`, не Gemini-генератор → без self-preference bias). **Calibration-ready (варіант B):** per-claim/per-source вердикти + fingerprint промпта + стабільні id у `report.json`; формальне κ-калібрування проти людських UA-міток — наступний трек (cross-lingual Fleiss ≈0.3 — головний ризик). Gold = **112 кейсів** (80 single-source з 50/50 claim/situation phrasing + 12 synthesis із конкретних прогнозів корпусу + 20 off-corpus), `build_generation_gold.py`. 8 задач TDD (subagent-driven). Design+plan: [`docs/generation/2026-06-25-generation-eval-design.md`](docs/generation/2026-06-25-generation-eval-design.md). Уся сюїта **310 тестів**. **Наступне (ручне):** рев'ю near_domain-питань + прогін `generation_eval.py` на реальній інфрі; далі формальне κ-калібрування судді, answer relevancy, citation precision (маркери [n]→id).
- **Generation-eval scope-ревізія (2026-06-27):** перший прогін (5 кейсів) виявив, що generation-eval ганяв реальний `AnswerOrchestrator` (retrieval у живій БД → генерація) — тобто тестував **увесь RAG**, не генерацію. Конфаунд: completeness карав генератор за **retrieval-промахи** (потрібний прогноз не знайшовся → recall падає, хоча винен retrieval); faithfulness ~0.5 частково через шум 10 retrieved джерел. **Рішення:** звузити generation-eval до **ізольованої генерації на gold-контексті** — метрики faithfulness + completeness, SUT = половина генерації (дано `expected_sources`), без живого retrieval. **Запарковано окремий трек** (чип `task_a358c756`): end-to-end RAG-eval + **поріг релевантності** в retrieval (зараз top-k без порога → система покладається на self-refusal Gemini); з порогом refusal стає детермінованим retrieval-рішенням і тестується там, а не в generation-eval. Дрібні фікси прогону: self-bootstrap `sys.path` (прямий запуск без PYTHONPATH), приглушено LiteLLM/httpx INFO-спам, `--limit` тепер реально обрізає кейси, прогрес-логування в `run_cases`/`run_eval`. v1-дизайн-док має баннер ревізії; **v2 = brainstorm→design→plan, не почато**. Сюїта **312 тестів**.
- **Generation-eval v2 — ізольована генерація (2026-06-29):** реалізовано ревізію й зведено в `main` (merge `edc9f75`). Прод `AnswerOrchestrator` розділено на `answer_from_sources` (generate-only) + `answer` (search→делегує, `query_orchestrator` опціональний); refusal прибрано з евалу повністю (scorer/промпти/метрики); **completeness судить фактично подані `run.result.sources`** (claim+situation як дезамбігуючий контекст), не заморожений gold-claim → нема divergence; **`ExpectedSource` несе повний заморожений `Prediction`** (build читає БД раз через `get_by_ids`) → eval-runtime **БД-free й відтворюваний**; gold перегенеровано (112 кейсів, 92 answerable, повні прогнози в `expected_sources[].prediction`). Дизайн і план **adversarial-reviewed** (3-критичні workflow проти реального коду: дизайн зловив 4 blocker-и до плану; план — sentinel для red-кроку метрик + локальні DB-імпорти). Impl — subagent-driven, 7 тасків, кожен spec-reviewed. **Знахідка першого прогону (limit=20):** faithfulness 0.60 був на **~90% інструментальним артефактом** — faithfulness-суддя бачив лише `render_sources` (id/claim/status), а генератору подавали ще date/target/confidence (`build_rag_prompt`), тож чесні echo цих полів каралися як галюцинації (у всіх 20 кейсах). **Фікс:** спільний `render_predictions` для генератора й судді → судять тотожне джерело → виправлена faithfulness ~0.96; реальна галюцинація лишилась ~1/20 (вигаданий суд/перенесення в a008). Сюїта **312 тестів**. Design+plan: [`docs/generation/2026-06-27-generation-eval-v2-design.md`](docs/generation/2026-06-27-generation-eval-v2-design.md) + `-plan.md`.
- **RAG answer contract — рерайт стилю відповіді (2026-06-29):** generation v1.5 свідомо лишив `RAG_SYSTEM` «без змін» — стиль відповіді ніколи не проєктували. Eval-прогін показав, що успадкований промпт **буквально наказує** дамп БД-запису (`cite confidence scores` / `verification status` / `accuracy statistics` / disclaimer) → у тексті для юзера лізли UUID джерела, «Рівень впевненості: 0.9», сирий enum «premature», вигадана «статистика точності». **Новий контракт:** прогноз→вердикт; статус перекладено простою мовою (confirmed→«справдився», refuted→«не справдився», unresolved→«оцінити не вдалося», premature→«ще зарано»); без UUID/числа confidence/сирого enum/вигаданих стат; один рядок дисклеймеру; author-agnostic (ім'я автора запарковано — потребує Person-join). **Підхід — промпт-онлі** рерайт `RAG_SYSTEM`+`RAG_TEMPLATE` (поданий контекст незмінний; не-лік досягається інструкцією); eval — петля зворотного зв'язку. guard-тест на відсутність лік-директив. brainstorm→design→plan→inline-impl→merge (`3291ba8`). Design+plan: [`docs/generation/2026-06-29-rag-answer-contract-design.md`](docs/generation/2026-06-29-rag-answer-contract-design.md) + `-plan.md`. **Наступне (рантайм):** перепрогін евалу підтвердить чистий стиль; запарковано — TG-бот: прямі посилання на канал автора (чип `task_ea2d0fea`).
- **Generation-eval — поведінкове підтвердження (2026-06-29):** повний прогін **92 кейси** (суддя Claude Opus) після фіксу #1 + рерайту промпту. **faithfulness 0.60→0.947** (hallucination 5%), **recall 1.0** (вкл. 12 синтез-кейсів — генератор не кидає джерела), **78/92 ідеальні 1.0**, 0 errors. Стиль **чистий**: leak-скан по всіх відповідях — UUID 0, «впевнен» 0, сирий enum 0, «статистик» 0 (6 збігів «%/успішн» — усі легітимний контент/вердикт, не вигадана стата). **Третя ітерація патерну «суддя ≠ те, що подано»:** залишкові ~5% — майже не галюцинації; у 13/14 неідеальних кейсів забракований claim — це сам **вердикт** («прогноз справдився»), бо faithfulness-суддя бачить поле `status`, але не приймає його як доказ результату (докази знає лише верифікатор, не RAG-джерело). Тобто справжня faithfulness ≈0.98+; **0.947 — консервативна нижня межа, не стеля**. **Дія (трек κ-калібрування):** інструктувати faithfulness-суддю, що `status` прогнозу — авторитетне джерело для вердикту. Реально варті уваги — одиниці (a076 — конкретна дата; a068 — світознавча елаборація).
- **Мінімальний AWS-деплой (2026-07-04):** «воно живе» — один EC2 + Docker Compose (Postgres+pgvector, migrate, app), SSH-only доступ, секрети з приватного S3, інфра як CloudFormation (`secrets`+`compute` стеки). Локальний bring-up зелений: postgres healthy → migrate exit 0 → app up → `/health`=200. Box-acceptance на реальному AWS — за користувачем (немає креденшелів у цій сесії). Design+plan+знахідки: [`docs/aws-deploy/`](docs/aws-deploy/).
- **RDS-міграція (2026-07-10):** durability-свап — Postgres-контейнер → RDS PostgreSQL 16.5+ (pgvector 0.8.0). Окремий CloudFormation `data`-стек (RDS + DB subnet group + SG-to-SG app→db), TLS-конект застосунку через `db_ssl_mode`/`make_engine` (asyncpg `ssl=require`, бо `rds.force_ssl=1`), Postgres лишився лише в локальному compose-override. Свіжий старт (re-ingest, без міграції даних). Юніт лише на `ssl_connect_args`; решта — cfn-lint + acceptance з durability-proof (знести/перестворити `compute` → дані в RDS живі). Design+plan: [`docs/aws-deploy/2026-07-10-rds-migration-design.md`](docs/aws-deploy/2026-07-10-rds-migration-design.md) + `-plan.md`. **Box-деплой за користувачем** (немає AWS-креденшелів у сесії).
- **Hybrid retrieval Частина B v1 (2026-07-11):** self-querying + typed фільтри реалізовано на гілці `feat/hybrid-retrieval` (9 тасків, subagent-driven TDD, кожен spec+quality-reviewed; **360 тестів**). `QueryPlanner` (Flash Lite temp 0) парсить NL-запит у `QueryPlan(semantic_query, SearchFilters)`; `PostgresVectorStore.search_similar` застосовує person/date-предикати `WHERE` на **exact-скані** (ANN нема → overfiltering не застосовний); `target_date` **null-inclusive** (Р2); невідомий автор → `REFUSAL_UNKNOWN_AUTHOR` з ім'ям (Р3); збій планера → `QueryPlanningError` → HTTP 500 (fail fast, Р4; аварійний обхід `query_planner_enabled=False`). Фейк `FakeVectorStore` дзеркалить SQL-семантику, паритет запінено mutation-тестами. Design+plan: [`docs/hybrid-retrieval/`](docs/hybrid-retrieval/). **Смоук — ПРОЙДЕНО end-to-end (2026-07-11):** локальну БД засіяно 173 реальними прогнозами Арестовича з `scripts/data/retrieval/corpus.json` (новий `scripts/retrieval/seed_corpus_from_json.py`, через продакшн-репозиторії), backfill ембедингів (OpenAI `text-embedding-3-small`) 173/173. 4 запити через `/answer` + `/query` на реальних Postgres+LLM, 0 помилок у логах: **(1)** автор+тема (Крим) — 10 джерел, дати 2021-2024; **(2)** автор+рік 2022 — 10 джерел, **усі 2022** (date-фільтр звузив: пор. Q1 без року — 2021-2024); **(3)** без автора (мобілізація) — семантичний пошук по всіх роках; **(4)** невідомий автор (Портников) → `REFUSAL_UNKNOWN_AUTHOR` з ім'ям, 0 джерел. `/query`-версія Q2 — явна перевірка: усі 10 `results[].prediction_date` = 2022 (`ALL 2022? True`). Фільтр-шлях + refusal + fail-fast підтверджено на живій інфрі. **Знахідка (pre-existing, поза скоупом):** помилка embedder-провайдера → сирий HTTP 500 — запарковано в Tech debt вище (чип `task_3ddadfdd`). Park (не змінилось): BM25/RRF, re-rank, entity-linking, формальний hybrid-eval, наповнення `target_date`.
- **Telegram-бот v1 (2026-07-11):** остання миля продукту — тонкий Q&A-фронтенд над `AnswerOrchestrator`: пакет `bot/` (texts/handlers/runner), aiogram long-polling asyncio-таскою в lifespan FastAPI (webhook неможливий — бокс SSH-only), stateless, author-agnostic, публічний без лімітів. Конфіг: нове поле `bot_enabled` (`telegram_bot_token` і aiogram були зарезервовані при скафолді); fail-fast без токена; `build_bot` у composition root, teardown через `AsyncExitStack`; смерть polling-таски → CRITICAL, API живе. +23 тести (сюїта 348). Runbook: `runbook/bot.md`. Смоук на живому токені — за користувачем. Design+plan: `docs/telegram-bot/`.
- **Telethon local-skip флаг (2026-07-12):** локальний `python -m prophet_checker` упав з `AuthKeyDuplicatedError`. Причина: той самий Telethon user-auth-key жив і на AWS-деплої (сесія вбудована/змонтована в бокс), і локально — Telegram убиває ключ, коли один auth-key бачить із двох IP одночасно. Ключ мертвий безповоротно; прод повернеться лише свіжим інтерактивним релогіном (окрема ручна дія за користувачем). Проти рецидиву — новий флаг `telegram_source_enabled` (default `True`, прод без змін): при `False` `build_orchestrator` не будує й не `start()`-ить tg-клієнт і віддає порожній `sources` (локальний інжест — no-op). Локальний `.env`=`false`; задокументовано в `.env.example`. Напрям (за користувачем): окремі сесії на EC2 й локалі — наразі достатньо скіпу локально. TDD, +1 тест (сюїта **385**); helper `_settings_with_test_env` пінить флаг `true` (детермінізм від ambient `.env`). Верифіковано живим запуском: `/health`=200, жодного `Connecting to …`/`AuthKeyDuplicatedError` у лозі.
- **SSH-доступ + SSM-розвідка (2026-07-15):** SSH на бокс таймаутив (`Operation timed out`): динамічний IP провайдера (Vodafone/UMC, пул `178.133.0.0/16`) блукає по /18-підблоках, а allowlist пінив старі `/32`, які протухають. **Зроблено:** у SSH-allowlist додано `178.133.0.0/16` (три старі `/32` лишено) — через параметр `SshIngressCidrs` стека `prophet-data`, change set, стек `UPDATE_COMPLETE` о 11:57Z. Не ручним CLI-`authorize`, щоб не плодити дрейф. **Пастка change-set:** з `--use-previous-template` трансформ `AWS::LanguageExtensions` (той `Fn::ForEach`) не розгортається → CFN каже «didn't contain changes»; лікується подачею шаблону через `--template-body` (плюс `CAPABILITY_AUTO_EXPAND`; решта параметрів `UsePreviousValue`, вкл. `DbPassword`). **⚠️ Латентний ризик (compute):** будь-який `update-stack` на `prophet-compute` пересоздасть інстанс і вб'є бокс — `LatestAmiId` резолвиться на найновіший AL2023, а живий бокс на старішому (`ami-0f926ea13b394bbf1` проти latest `ami-028acd58719b05107`). Перед наступним деплоєм compute — запінити AMI. **SSM-міграція — PARKED:** мета — перевести SSH-скрипти на SSM Session Manager і закрити порт 22 (динамічний IP перестане боліти). Готовність: роль `prophet-compute-InstanceRole` без `AmazonSSMManagedInstanceCore` (лише inline `read-secrets-bucket`) → бокс не зареєстрований у SSM; локально стоїть `session-manager-plugin 1.2.835.0`. Безпечне вмикання — прямий `aws iam attach-role-policy` (не через compute-CFN, бо AMI-міна). Обсяг (котрі зі скриптів connect/logs/deploy/psql/refresh/status/secrets) не вирішено.
- **`deploy/ingest.sh` — тригер прод-інжесту (2026-07-15):** новий скрипт-побратим до `deploy.sh`/`logs.sh` — запускає один цикл інжесту на живому боксі однією командою. Резолвить бокс → SSH → `curl -X POST localhost:8000/ingest/run` **на боксі** (порт 8000 лише на localhost боксу, тому не з локалі) → синхронно чекає `CycleReport` і друкує підсумок (`jq` best-effort: тотали + канали з помилками, фолбек на сирий JSON). Інжест мутує прод (пише прогнози, рухає курсори, LLM-гроші) → gate підтвердженням з `-y` bypass, як `deploy.sh`. Ще: `--dry-run`/`--help`, конфігуровний `TIMEOUT` (curl `-m`, дефолт 900с), SSH keepalive (цикл мовчить хвилинами), вердикт за HTTP-кодом (503→«не готовий», 500→`logs.sh`, 000/нема-маркера→`status.sh`). Локальні перевірки зелені (`bash -n`, `--help`, `--dry-run`, розбір відповіді на семпл-`CycleReport`); коміт `8116f2c` (ff-merge гілки `feat/deploy-ingest` у `main`). **Прод-прогін — ЗРОБЛЕНО 2026-07-15 (2238 документів), див. запис нижче.** Runbook: [`runbook/ingest.md`](runbook/ingest.md).
- **`POST /verify/run` + `deploy/verify.sh` — тригер прод-верифікації (2026-07-15).** Досі верифікація не мала шляху на бокс: ендпоінта не було (лише `/ingest,/query,/answer,/health`), деплой-скрипта теж, а CLI `run_verification_cycle.py` навіть не пакується в Docker-образ (`Dockerfile` копіює лише `src`+`alembic`). Закрито дзеркалом інжест-патерну: **(1)** ендпоінт `POST /verify/run` в `app.py` — верифікаційний оркестратор будується в `lifespan` (`build_verification_orchestrator`) і лягає на `app.state.verification_orchestrator`; опційний query-параметр `?limit=N` → `run_cycle(limit=N)`; 503 якщо не піднятий, 500 на катастрофу (тече лише тип винятку). **(2)** `deploy/verify.sh` — копія `ingest.sh` (резолв боксу → SSH → curl на localhost:8000 боксу → підсумок), плюс `--limit N` (додає query-рядок) і gate підтвердження (верифікація пише статуси в прод-БД + 2 LLM-виклики/прогноз). Це **first-pass** (`get_unverified`, attempt-cap<5); recheck-луп лишається запаркованим. TDD: +5 ендпоінт-тестів (`test_app_endpoints.py`) і +6 герметичних скрипт-тестів (`test_deploy_verify.py`, фейкові aws/ssh); уся сюїта **зелена**. Локальні перевірки: `bash -n`, `--help`, `--dry-run --limit 5` (друкує `POST …/verify/run?limit=5`). **Прод-прогін — ЗРОБЛЕНО 2026-07-16, див. запис нижче.** Runbook: [`runbook/verify.md`](runbook/verify.md).

- **Повний прод-прогін пайплайну — інжест + верифікація (2026-07-15/16).** Обидві половини відпрацювали на живій AWS-інфрі, не локально й не на семплах.

  **Інжест — 2026-07-15.** Усі **2238 документів** записані того дня, з 08:34:46 до 15:10:49 (`min/max(raw_documents.collected_at)`). Найновіший пост у корпусі — `published_at = 2026-07-14 13:42`, тобто збір **догнав канал під нуль**, а не спинився на середині. Курсор став на `2026-07-14 18:00:22Z`.

  **Верифікація — 2026-07-16.** Лог боксу, 07:30:18Z: `verification done: verified=2111 failed=0 skipped=0` — нуль падінь, нуль пропусків; прогрес логувався кожні 50.

  **Підсумок по корпусу** (зріз прод-RDS на 2026-07-18, `deploy/psql.sh --stats`): 2238 документів + **4116 прогнозів, усі 4116 з `verified_at`** — покриття 100%; разом 6354 записи по одному автору (Арестович, `@O_Arestovich_official`). Розподіл вердиктів: `unresolved` 1894 (46%), `confirmed` 1064 (26%), `refuted` 764 (19%), `premature` 394 (9.6%) — сума сходиться з 4116. Це перший еталон розподілу статусів на повному корпусі; 394 `premature` — рівно та популяція, за якою прийде recheck-луп.

  **Два застереження:** (1) доступне вікно логу покриває прогін верифікації на 2111 прогнозів — решту до 4116 закрив раніший прогін, що у вікно не потрапив, тож усі 4116 одному запуску не приписуються; (2) `raw_documents.processed = 0` для всіх 2238 документів — див. Tech debt нижче.

  > **Пастка читання курсора (обпікся 2026-07-18).** `person_sources.last_collected_at` зберігає **`published_at` останнього обробленого поста**, а не час прогону (`ingestion/orchestrator.py` — `update_source_cursor(ps.id, raw_doc.published_at)`). Тому `lag = now() - cursor` міряє **свіжість контенту, не свіжість прогону**: якщо автор мовчить тиждень, lag росте на тиждень при щоденному інжесті. Судити «чи ганявся інжест» треба по `max(raw_documents.collected_at)`, а не по курсору.

- **Query logging — слід публічних запитів бота (2026-07-20, гілка `feat/query-logging`, смоук за користувачем).** Перед відкриттям бота на загал не було відповіді на «що люди питають»: текст запиту писався на `DEBUG` при `log_level=INFO`, тобто **нікуди**, а те, що таки писалось на INFO (`user_id`, довжина, латентність), жило в `docker compose logs` до наступного `deploy.sh`. Закрито таблицею `query_logs` (`user_id` **BigInteger** — Telegram id не влазить в int32; `question`, `answer` nullable, `latency_ms`, `created_at` + індекс) і зрізом `deploy/psql.sh --queries` (24г/7д: запити, унікальні юзери, збої, p50/p95; топ-10 активних; останні 20 запитів текстом).

  **Гарантія, заради якої це так побудовано:** збій запису не має права зламати відповідь. `_record_query` тримає власний `try/except` на місці виклику — впала БД, юзер усе одно отримує відповідь, у лозі `query log write failed`. Покрито тестом, і тест **перевірено мутацією**: без `try/except` він падає, тобто не вакуумний.

  **Доменної моделі навмисно нема** — запис їде одним хопом (хендлер → `save()` → INSERT) і читається лише через psql, тож міст domain↔db був би церемонією. Репозиторій бере типізовані параметри; ORM-тип за межі `storage/` не тече.

  **Свідомо не входить (відхилив користувач, наслідки зафіксовані в дизайні):** класифікація вердикту ⇒ **частку відмов заднім числом не порахувати** (у рядку нема структурної ознаки відмови, а матчити текст крихко); `source_ids` ⇒ крива відповідь видима, але не відтворювана — частково закриється, якщо увімкнути `citations_enabled`.

  **Знахідка рев'ю, варта запису:** перша версія рахувала `latency_ms` **до** запису в БД, а `elapsed` у лог-рядку — **після**, тож два числа про той самий запит розходились (перевірено: 102 мс проти 0.4 с при штучно повільному записі). Тепер обидва з одного виміру, знятого одразу після відповіді оркестратора — **метрика не роздуває себе власним записом**. Заразом хелпер перейменовано `_log_query` → `_record_query`: у цьому файлі «log» уже означає рядок у процесний лог, а тут довговічний запис у БД.

  **Побічна знахідка:** `complexipy` не був встановлений у `.venv` — тобто **pre-commit гейт когнітивної складності насправді не працював**. Полагоджено `uv sync --extra dev`. Окремо: `ruff format` у репо не забезпечується взагалі (25 файлів дрейфують) — не чіпав, це загальний борг.

  Design+plan: [`docs/observability/`](docs/observability/). Сюїта **473**. Смоук на живій БД і зведення в `main` — за користувачем.
