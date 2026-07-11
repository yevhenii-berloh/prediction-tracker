# Hybrid Retrieval, Частина B v1 — Design

**Дата:** 2026-07-11
**Статус:** запропоновано
**Трек:** retrieval (продовження `docs/query-serving/`, `docs/retrieval-eval/`)
**Дослідження-основа:** Brain-вікі `wiki/concepts/hybrid-rag-search.md` (deep-research 2026-07-04/10); дистилят у `progress.md` («Дослідження — hybrid structured+unstructured RAG search»)

---

## 1. Проблема

Ретрив у `/query` та `/answer` — чистий семантичний пошук по embedding
(`claim+situation`). Він провалюється на чотирьох класах запитів:

1. Запит називає **автора** — імені нема в тексті прогнозу, embedding його не кодує.
2. Запит обмежує **час, коли прогноз зроблено** («що казав у 2022»).
3. Запит обмежує **час, про який прогноз** («прогнози на 2023»).
4. Embedding слабко розрізняє роки та власні назви загалом.

Дані для розв'язку вже в схемі: `person_id`, `prediction_date` (NOT NULL),
`target_date` (nullable, ~70–90% null). Вони просто не беруть участі в пошуку.

## 2. Рішення (огляд)

Двостадійний retrieval за патернами 1–3 дослідження:

1. **Query understanding.** Новий компонент `QueryPlanner`: вторинний LLM-виклик
   перетворює NL-питання на `QueryPlan` = семантичний запит + типізовані фільтри.
2. **Фільтрований векторний пошук.** `VectorStore.search_similar` приймає опційні
   `SearchFilters`; Postgres-реалізація додає `WHERE`-предикати до наявного
   exact-скану.

Чому це безпечно саме зараз: у нас **немає ANN-індексу** — `search_similar` робить
exact scan (`ORDER BY cosine_distance LIMIT k`). Тому pgvector post-filtering
gotcha (overfiltering) не застосовна: `WHERE` на exact-скані — це справжній
pre-filter зі 100% recall. Це і є рекомендація pgvector docs для малих корпусів.

## 3. Скоуп

**У v1:**

- Фільтри: автор (`person_id`), дві дати (`prediction_date`, `target_date`).
- Self-querying LLM-стадія (`QueryPlanner`).
- Прокидання фільтрів через `VectorStore` Protocol + Postgres-реалізацію + fakes.
- Конфіг-перемикач `query_planner_enabled`.

**Свідомо поза скоупом (park):**

- BM25 / lexical fusion (RRF), re-ranking — окрема ітерація, потребує tsvector-індексу.
- Entity-linking / alias-таблиця імен — YAGNI, поки персон у корпусі одиниці;
  список персон цілком влазить у промпт планера.
- ANN-індекс (HNSW) і two-stage candidate-set — актуально лише при рості корпусу;
  міграція ізольована всередині `PostgresVectorStore`, консумерів не зачепить.
- Формальний hybrid-eval (gold із фільтр-інтентами) — у запаркований трек
  end-to-end RAG-eval (чип `task_a358c756`). Ця ітерація: TDD + ручний смоук.
- Наповнення `target_date` (70–90% null) — окрема проблема інжесту/верифікації.

## 4. Ключові рішення

| # | Рішення | Чому |
|---|---------|------|
| Р1 | Обидві дати як окремі фільтри; LLM розрізняє «коли сказано» від «про який час» | Focus-time модель із temporal-IR: це різні інтенти, змішувати їх — джерело провалів |
| Р2 | `target_date`-фільтр **null-inclusive**: `(target_date у діапазоні OR target_date IS NULL)` | 70–90% null; суворий фільтр викине прогнози про потрібний період, де дату не витягли. Фільтр працює як виключаючий, не вибираючий |
| Р3 | Автор поза корпусом → **чесна відмова**, не тихе скидання фільтра | Спитали про Портникова — відповідь про Арестовича гірша за «не знаю» для trust-продукту |
| Р4 | Збій/нерозпарсюваність планера → **пошук без фільтрів** + WARNING | Деградація до сьогоднішньої поведінки; транзитна помилка LLM не має класти пошук |
| Р5 | Ембедимо `semantic_query` (запит без автора/дат), не оригінальне питання | Автор/дати вже у фільтрах; лишати їх у векторі — шум, який дослідження радить прибрати |
| Р6 | Планер — на production-моделі (Flash Lite, temperature 0) | Дешево, детерміновано (перевірено verifier-треком), та сама модель що й генерація |
| Р7 | Без ANN-індексу; фільтри = `WHERE` на exact-скані | Див. §2; overfiltering-міркування відкладені до росту корпусу |

## 5. Компоненти та контракти

### 5.1 Доменні моделі (`models/domain.py`)

```python
class SearchFilters(BaseModel):
    person_id: str | None = None
    author_unknown: bool = False
    prediction_date_from: date | None = None
    prediction_date_to: date | None = None
    target_date_from: date | None = None
    target_date_to: date | None = None

class QueryPlan(BaseModel):
    semantic_query: str
    filters: SearchFilters
```

Семантика `SearchFilters`:

- Усі присутні поля з'єднуються через AND.
- `None` — предиката нема.
- Пара `*_from`/`*_to` — незалежні межі (`>=` / `<=`); рік розгортається планером
  у `01-01`…`12-31`.
- `target_date`-предикат завжди null-inclusive (Р2).
- `author_unknown=True` — автора названо, але його нема серед відомих персон;
  сигнал для відмови (Р3), а не предикат.

### 5.2 `QueryPlanner` (`query/planner.py`)

```python
class QueryPlanner:
    def __init__(self, llm: LLMClient, person_repo: PersonRepository) -> None: ...
    async def plan(self, question: str) -> QueryPlan: ...
```

Поведінка `plan`:

1. Читає персон через `PersonRepository.list_all()` (без кешу — таблиця крихітна).
2. Будує промпт: схема фільтрованих полів (ім'я + опис + тип, за патерном
   AttributeInfo), список персон `name → id`, сьогоднішня дата (для відносних
   виразів: «минулого року», «нещодавно»), few-shot приклади.
3. Викликає `LLMClient.complete` (temperature 0).
4. Парсить відповідь через `parse_query_plan()` → `QueryPlan`.
5. Будь-який збій кроків 3–4 → passthrough-план:
   `QueryPlan(semantic_query=question, filters=SearchFilters())` + WARNING (Р4).

### 5.3 Промпт-контракт (`llm/prompts.py`)

- `SELF_QUERY_SYSTEM` + `build_self_query_prompt(question, persons, today)`.
- Вихід LLM — JSON з полями `semantic_query`, `person_id`, `author_unknown`,
  чотири дати (ISO або null).
- Інструкції промпта:
  - «коли сказано» → `prediction_date_*`; «про який час прогноз» → `target_date_*` (Р1);
  - автор згаданий, але його нема в списку → `author_unknown=true`, `person_id=null`;
  - автор не згаданий → обидва поля порожні;
  - відносні дати прив'язувати до переданої сьогоднішньої дати;
  - `semantic_query` — питання без автора й дат; якщо після зняття нічого не
    лишилось — переказ теми питання.
- `parse_query_plan(raw, known_person_ids) -> QueryPlan` — typed boundary
  (Pydantic, не dict); список відомих id потрібен для валідації `person_id` (§7).
  Валідації парсера — в таблиці помилок (§7).

### 5.4 `VectorStore` Protocol (`storage/interfaces.py`)

```python
async def search_similar(
    self,
    query_embedding: list[float],
    limit: int = 10,
    filters: SearchFilters | None = None,
) -> list[VectorMatch]: ...
```

`filters=None` — поведінка як сьогодні (backward compatible). Postgres-реалізація
транслює фільтри в `WHERE`-предикати поверх наявного exact-скану;
`FakeVectorStore` у `tests/fakes.py` реалізує ту саму семантику in-memory
(включно з null-inclusive) — фейк оновлюється разом із Protocol.

### 5.5 `QueryOrchestrator` (`query/orchestrator.py`)

Нова опційна залежність: `planner: QueryPlanner | None = None`.

Потік `search(question, limit)`:

1. Планер відсутній → сьогоднішній потік без змін.
2. `plan = await planner.plan(question)`.
3. `plan.filters.author_unknown` → одразу порожній `QueryResult` (без embed і пошуку).
4. `embedding = embed(plan.semantic_query)` (Р5).
5. `search_similar(embedding, limit, plan.filters)`.
6. Далі без змін: threshold → `get_by_ids` → rank.

### 5.6 Що НЕ змінюється

- `AnswerOrchestrator`, `/query`- і `/answer`-ендпоінти, refusal-логіка:
  порожній `QueryResult` уже веде до `REFUSAL_NO_DATA`.
- Схема БД, міграції, embedding-колонка, інжест.

### 5.7 Wiring (`factory.py`, `config.py`)

- `build_query_orchestrator`: + `LLMClient` (та сама модель, що для генерації),
  + `PersonRepository`, + `QueryPlanner`; планер передається в `QueryOrchestrator`.
- `config.py`: `query_planner_enabled: bool = True` (прецедент —
  `embeddings_enabled`). `False` → планер не створюється, потік як сьогодні.

## 6. Потік даних (answer-шлях)

```
NL-питання
  → AnswerOrchestrator.answer
    → QueryOrchestrator.search
        → QueryPlanner.plan ──(LLM, temp 0)──► QueryPlan(semantic_query, filters)
        → author_unknown? ──► порожній QueryResult ──► REFUSAL_NO_DATA
        → embed(semantic_query)
        → search_similar(embedding, limit, filters)   # exact scan + WHERE
        → threshold → get_by_ids → rank
    → build_rag_prompt → LLM → відповідь
```

## 7. Обробка помилок

| Збій | Де ловиться | Поведінка |
|------|-------------|-----------|
| LLM-виклик планера кинув виняток | `QueryPlanner.plan` | WARNING; passthrough-план (оригінальне питання, порожні фільтри) |
| Відповідь LLM не парситься / не валідна JSON-схема | `parse_query_plan` → `plan` | те саме |
| `person_id` від LLM нема серед відомих | `parse_query_plan` (валідація проти переданого списку) | `author_unknown=True` → відмова. Консервативно: краще відмовити, ніж відповісти не тим автором |
| `*_from > *_to` (інвертований діапазон) | `parse_query_plan` | скинути обидві межі цієї пари + WARNING |
| Порожній `semantic_query` | `parse_query_plan` | підставити оригінальне питання |
| 0 результатів після фільтрів | наявний потік | порожній `QueryResult` → `REFUSAL_NO_DATA` (Р3) |

Логування: WARNING на кожен fallback зі стабільним префіксом; на успішний план —
DEBUG із витягнутими фільтрами (без тексту питання в INFO — політика логів).

## 8. Тестування (стратегія)

Повний TDD-список кроків — у плані. Рівні:

- **Юніти (fakes, без мережі):**
  - `parse_query_plan`: валідний план; зламаний JSON; невідомий `person_id`;
    інвертовані дати; порожній `semantic_query`.
  - `build_self_query_prompt`: персони в промпті; сьогоднішня дата; схема полів.
  - `QueryPlanner` + FakeLLM: happy path; виняток LLM → passthrough.
  - `QueryOrchestrator` + фейк-планер: фільтри доходять до `search_similar`;
    `author_unknown` → порожній результат без embed; `planner=None` → стара
    поведінка (регресія).
  - `FakeVectorStore.search_similar` з фільтрами: кожен предикат окремо +
    null-inclusive семантика `target_date`.
- **Смоук (реальна БД + LLM, вручну):** 4 запити — «автор + рік сказання»,
  «прогнози на рік» (target), питання без фільтрів, невідомий автор → відмова.
  Прогін через `/answer` на локальному compose.

## 9. Ризики та відкриті питання

- **Якість плану на UA/RU-питаннях** — few-shot у промпті; якщо промаху багато,
  наступний крок — формальний eval (запаркований трек), не тюнінг наосліп.
- **Latency:** +1 LLM-виклик (~0.5–1 с) на запит. Прийнятно для pet-scale;
  `query_planner_enabled=False` — аварійний вимикач.
- **`target_date` null-rate:** фільтр null-inclusive, тож користь від
  `target_date`-предиката зростатиме з наповненням колонки — без змін коду.

## 10. Посилання

- Дослідження: Brain `wiki/concepts/hybrid-rag-search.md` (патерни 1–3, pgvector-специфіка)
- Гайд: Brain `wiki/concepts/rag-implementation-guide.md` (Частина B)
- Попередники: `docs/query-serving/2026-06-22-query-serving-design.md`,
  `docs/retrieval-eval/2026-06-19-retrieval-eval-design.md`
- Multi-Meta-RAG — https://arxiv.org/pdf/2406.13213
- LangChain SelfQueryRetriever — https://python.langchain.com/docs/how_to/self_query/
- pgvector filtering — https://docs.pgedge.com/pgvector/v0-8-1/filtering/
