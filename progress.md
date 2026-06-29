# Prediction Tracker — Progress Log

Living log: time, cost, deliverables. Оновлюється коли завершується milestone або значуща задача.
Project-wide джерело правди по статусу; per-track деталі — у `docs/<track>/README.md`.

**Останнє оновлення:** 2026-06-29

---

## Current state (snapshot 2026-06-29)

| Metric | Value |
|--------|-------|
| Календарний час від старту | ~82 днів (2026-04-08 → 2026-06-29) |
| Commits | 344 (push відновлено, синхронізовано з origin) |
| Tests passing | 312 |
| Tasks completed | M1 (5/5) + M2 (5/5) + M2.5 (eval/data) + Ingestion→production track + Verifier-v2 track (19.5→19.9 + Task 20) + RAG-трек (retrieval → query → generation → eval v2 + answer-contract) |
| Tasks in flight | — |
| Tasks queued | Recheck-луп, AWS deploy, GitHub Actions CI |
| AWS cost | $0 (not deployed yet) |

**Активний фокус:** RAG-трек завершено по коду й зведено в `main`: retrieval → query → generation
(`POST /answer`) → **generation-eval v2** (ізольована генерація на **заморожених** gold-прогнозах;
faithfulness+completeness) + **RAG answer-contract** (відповідь прогноз→вердикт простою мовою, без
службових полів). Перший eval-прогін показав, що faithfulness 0.60 був ~90% **інструментальним
артефактом** (суддя не бачив date/confidence, які бачив генератор) → фікс: спільний `render_predictions`
для генератора й судді → **підтверджено повним прогоном (92 кейси): faithfulness 0.60→0.947,
recall 1.0, стиль чистий**. **Наступне:** κ-калібрування судді (вкл. трактування `status` як
авторитету для вердикту — див. нотатку нижче). Park: end-to-end RAG-eval + поріг релевантності
(чип `task_a358c756`), recheck-луп verifier, AWS deploy.

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

## Phase 6: AWS deploy + CI 📋 QUEUED

| Task | Статус |
|------|--------|
| 23 — AWS RDS PostgreSQL + pgvector | 📋 |
| 24 — AWS EC2 + Docker deploy | 📋 |
| 20 (master-plan) — GitHub Actions CI | 📋 (опціонально, після deploy) |

---

## Phase 7: Future (post-MVP)

1. **Verifier recheck-луп** — повторна перевірка `premature` за `next_check_at` до `max_horizon` (urgency-поля вже пишуться у Task 20).
2. Detection prefilter (`PredictionDetector`) — якщо two-tier.
3. Telegram bot frontend + RAG query endpoint.
4. News collector (Task 22) — для verifier evidence.
5. Continuous eval-loop (production quality monitoring).

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
