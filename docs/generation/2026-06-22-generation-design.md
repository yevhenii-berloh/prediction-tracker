# RAG Answer Generation (v1.5) — Design

**Дата:** 2026-06-22
**Status:** 📋 designed — pre-implementation
**Контур:** генерація відповіді поверх retrieval
([`../query-serving/2026-06-22-query-serving-design.md`](../query-serving/2026-06-22-query-serving-design.md)).

---

## Мета

Перетворити `QueryResult` (ранжовані прогнози) на **зв'язну відповідь українською** з посиланням
на джерела: `POST /answer {question, limit}` → `AnswerResult {query, answer, sources}`.

## Рамка рішень (узгоджено в брейнштормі)

- **Pipeline-only.** Жодного eval якості (faithfulness/citation/refusal-вимірювання) — окремий
  трек пізніше. Будуємо генерацію + endpoint + гартування промпта.
- **Цитування = answer + sources.** `AnswerResult` несе згенерований текст і структурований
  список `sources` (ті самі top-k `RetrievedPrediction`, на яких ґрунтувалась відповідь). Без
  крихкого парсингу маркерів. Точну по-клеймну прив'язку лишаємо на потім.
- **Refusal = short-circuit на порожні sources.** Якщо retrieval повернув 0 прогнозів → canned
  відповідь `REFUSAL_NO_DATA` **без виклику LLM** (дешево, нуль галюцинацій). Поріг релевантності
  запаркований (непідібраний) → не вводимо.
- **Чистий шов:** `AnswerOrchestrator` переюзує `QueryOrchestrator.search()`, не дублює retrieval;
  `QueryOrchestrator` лишається суто-retrieval.

## Архітектура

```
POST /answer {question, limit}
        │
        ▼
AnswerOrchestrator.answer(question, limit)
        │  result = QueryOrchestrator.search(question, limit)   ← переюз retrieval
        │  if not result.results:  → AnswerResult(answer=REFUSAL_NO_DATA, sources=[])   (без LLM)
        │  prompt = build_rag_prompt(question, result.results)
        │  text   = LLMClient.complete(prompt, system=RAG_SYSTEM)   ← Gemini Flash Lite, temp=0
        ▼
AnswerResult {query, answer, sources=result.results}
```

## Доменна модель (`models/domain.py`)

```python
class AnswerResult(BaseModel):
    query: str
    answer: str
    sources: list[RetrievedPrediction]
```

## Компоненти та файли

### 1. `build_rag_prompt` — гартування (`llm/prompts.py`)
Зараз: `build_rag_prompt(question, predictions_context: list[dict]) -> str` — **magic-dict**
(проти CLAUDE.md:80) і контекст без `id`/дат (LLM не може ні цитувати, ні дати назвати). Єдиний
викликач — тест (прод-споживачів нема), тож міняю **in place** на типізований вхід.

Нова сигнатура:
```python
def build_rag_prompt(question: str, sources: list[RetrievedPrediction]) -> str: ...
```
**Поведінка:** для кожного source рядок контексту включає `prediction.id`, `claim_text`, `situation`
(якщо є), `prediction_date`, `target_date` (якщо є), `status.value`, `confidence` — щоб LLM міг
посилатись на конкретні прогнози й називати дати. `RAG_SYSTEM` лишається без змін. Повна реалізація — у плані.

### 2. `AnswerOrchestrator` (`query/answer_orchestrator.py`) — NEW

Сигнатура:
```python
class AnswerOrchestrator:
    def __init__(self, query_orchestrator: QueryOrchestrator, llm: LLMClient) -> None: ...
    async def answer(self, question: str, limit: int = 10) -> AnswerResult: ...
```

**Поведінка `answer`:**
- Викликає `query_orchestrator.search(question, limit)`.
- Якщо `results` порожні → повертає `AnswerResult(answer=REFUSAL_NO_DATA, sources=[])` **без виклику LLM**.
- Інакше → `build_rag_prompt(question, results)` + `llm.complete(prompt, system=RAG_SYSTEM)` → `AnswerResult(answer=text.strip(), sources=results)`.

**Константа:** `REFUSAL_NO_DATA: str` — canned відмова (текст у плані).

**Логування:** per-module `logger`; INFO на milestone з лічильником (без payload); помилки **не** ловить (спливають до endpoint — CLAUDE.md:105). Повна реалізація — у плані.

### 3. `POST /answer` (`app.py`) — NEW

**Request-модель:**
```python
class AnswerRequest(BaseModel):
    question: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
```

**Endpoint:** `POST /answer` → `response_model=AnswerResult`. Патерн ідентичний наявному `POST /query`: `getattr(app.state, "answer_orchestrator", None)` → 503 якщо не ініціалізовано; делегує `ao.answer(req.question, req.limit)`; `except Exception` → `logger.exception` + 500. Повна реалізація — у плані.

### 4. `factory.build_answer_orchestrator(settings, stack)` — NEW
Будує `QueryOrchestrator` (через наявний `build_query_orchestrator`) + `LLMClient(provider="gemini",
model="gemini-3.1-flash-lite-preview", api_key=settings.gemini_api_key, temperature=0)` →
`AnswerOrchestrator`. `lifespan` кладе на `app.state.answer_orchestrator`.

## Потік даних та обробка помилок

| Ситуація | Поведінка |
|----------|-----------|
| Порожнє `question` | 422 (Pydantic `min_length=1`) |
| 0 знайдених прогнозів | 200, `answer=REFUSAL_NO_DATA`, `sources=[]`, **без виклику LLM** |
| Є sources | LLM генерує відповідь, ґрунтуючись на них |
| Збій LLM / БД | 500, `logger.exception(...)` |

Логування — `logger` (без `print()`); INFO-рядок `answer: sources=%d` (без тексту запиту/відповіді
як payload — CLAUDE.md:106).

## Тестування (unit на фейках, без БД/мережі/реального LLM)

- `AnswerResult` модель: конструюється, несе `sources`.
- `build_rag_prompt(question, sources)`: текст містить `question`, `id`, дату й статус кожного джерела.
- `AnswerOrchestrator.answer`:
  - порожні `results` → `REFUSAL_NO_DATA`, `sources=[]`, **LLM не викликано** (fake LLM лічильник=0);
  - непорожні → LLM викликано з промптом, що містить джерела; повертає `answer`+`sources`.
- `POST /answer`: 422 (порожнє), 503 (не ініціалізовано), 200 happy (fake orchestrator на
  `app.state`, ідіом `test_app_endpoints`).

## Скоуп

**In:** `AnswerResult`, гартований `build_rag_prompt`, `AnswerOrchestrator`, `POST /answer`,
`build_answer_orchestrator`, unit-тести.

**Out (deferred):**
- **Eval генерації** (faithfulness / citation precision / refusal correctness; Ragas/Trust-Score) —
  окремий трек.
- **Маркерні цитати** [n]→id — варіант C, ближчий до eval-grade.
- **Поріг релевантності / refusal за слабким матчем** — потребує тюнінгу по gold.
- **Telegram-бот** — окремий фронтенд-цикл (споживає `/answer`).
- **Кешування, multi-turn, query-transform** — post-MVP.

## Очікуваний результат

Робочий `POST /answer`, що на питання UA повертає зв'язну відповідь із посиланням на конкретні
прогнози (`sources`), коректно відмовляється за відсутності даних, і не галюцинує на порожньому
корпусі (short-circuit). Якість — не виміряна (eval — наступний трек).
