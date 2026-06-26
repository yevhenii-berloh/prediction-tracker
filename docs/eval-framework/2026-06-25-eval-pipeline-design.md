# Eval Pipeline (`eval_common`) — Design

**Дата:** 2026-06-25
**Status:** 📋 designed — pre-implementation
**Контур:** спільний каркас для всіх eval-скриптів (`scripts/eval_common/`); перший консумер — generation-eval.
**Дослідження:** [`../generation/2026-06-25-eval-research-summary.md`](../generation/2026-06-25-eval-research-summary.md)
(висновок: будувати тонкий власний харнес, переюзати визначення метрик + структуру).

---

## Мета

Винести спільний кістяк, який усі евали проєкту вже наближено мають, в один типізований каркас
`eval_common`, щоб:

1. Новий eval писався як **dataset → run → score → aggregate → report**, не копіюючи інфраструктуру.
2. Зникло задокументоване дублювання (client-builder ×4, MD-рендер ×3, JSON-IO ×6, throttle-таблиці,
   що крихко мутуються ззовні `detection_eval`).
3. «Безпідставно різні» рішення (формат звіту, per-item-персистенція, concurrency, cost) звелись до
   **однієї конвенції**.

**Не мета (поза цим доком):** конкретні скорери/датасет/judge-промпти/набір метрик generation-євалу —
це окремий дизайн-док, що споживає цей каркас. Ретрофіт 4 наявних евалів — пізніше.

## Рамка рішень (узгоджено в брейнштормі)

- **Тонкий власний каркас, не фреймворк.** Ragas/DeepEval — самі LLM-judge/NLI калькулятори; їхня
  цінність (визначення метрик + структура) інтерналізується без важкої залежності, що дублювала б
  наш [[litellm]]-шар і повертала magic-dict-и. Деталі — у дослідженні.
- **Каркас узагальнений, generation — driving-приклад.** Контракти валідуються першим консумером, але
  лишаються узагальненими (не прив'язаними до конкретного евалу).
- **Композиція — гібрид (не обов'язковий `Evaluator` ABC).** `eval_common` дає композовні роль-функції
  + тонкий опціональний `run_eval()` для типового single-pass випадку. Багатостадійні евали
  (як `extraction_quality`) композують роль-функції напряму.
- **Share prod code, don't fork.** Runner кличе реальний прод-компонент через адаптер, що постачає eval.

---

## Архітектура: ролі + потік даних

```
dataset-file (або інлайн/синтез)
   │  [dataset loader]   чистий IO + валідація; розмітка опціональна (див. «Походження датасету»)
   ▼
EvalCase[]
   │  [Runner]           кличе SUT-адаптер; concurrency + throttle + ізоляція помилок
   ▼
EvalRun[]
   │  [Scorer×N]         кожен скорер: EvalRun → ScoreCard (може кликати Judge/ембединги)
   ▼
ScoredRun[]
   │  [Aggregator]       чиста арифметика → корпус-метрики
   ▼
Metrics
   │  [Reporter]         JSON (повний) + MD (людський) + metadata
   ▼
scripts/outputs/<eval>_eval/
```

**Навіщо розбивати на окремі стадії** (а не один цикл «згенеруй-і-одразу-оціни»). Спільна ідея:
відокремити дороге/недетерміноване (LLM-виклики) від дешевого/детермінованого (диск, арифметика).

- **Генерація окремо від оцінювання → не платиш за генерацію двічі.** Runner один раз пише
  `EvalRun[]` (згенеровані відповіді) на диск. Підкрутив суддю (рубрика, новий промпт, ще одна
  метрика) — ре-скориш по збережених відповідях, без повторної генерації. Переганяєш генерацію лише
  коли змінилась **сама система** (нова модель/промпт у проді).
- **Оцінювання окремо від зведення → тестуєш математику без LLM.** Scorer дає per-item картки (через
  LLM-суддю, дорого/недетерміновано); Aggregator згортає їх у числа (чиста арифметика). Розділені —
  Aggregator стає чистою функцією: годуєш фейкові `ScoreCard` і ассертиш `hallucination_rate == 0.3`
  детерміновано, без мережі. Складні випадки (матриці, ділення на нуль) покриваються unit-тестами.
- **Один примітив concurrency на обидва місця LLM-фан-ауту.** І генерація (Runner кличе SUT), і
  суддівство (Scorer кличе Judge) потребують однакового semaphore+throttle+ізоляції помилок — той
  самий `run_cases` обслуговує обидві осі, машинерія не дублюється.

### Каркас не RAG-специфічний — мапінг наявних евалів

Усі 4 наявні евали (карта в дослідженні) і новий generation лягають на ті самі ролі — розходиться лише
**вага скорер↔aggregator** і тип скорера, що й має бути специфікою консумера:

| Eval | `input` | `result` | Scorer | Aggregator |
|---|---|---|---|---|
| detection | текст поста | `bool` | тонкий: pred vs gold | **товстий: P/R/F1** |
| extraction-quality | текст поста | claims | **товстий: LLM-judge 6 вердиктів** | halluc-rate, розподіл |
| verification | прогноз | status/strength/value | тонкий: vs gold ×3 осі | **товстий: 3× accuracy+confusion+calibration** |
| retrieval | запит | ranked ids | тонкий: hit@k / RR | recall@k, MRR |
| **generation** (новий) | питання | `AnswerResult` | **товстий: faithfulness/citation judge + relevancy** | тонкий: середні |

Класифікація/ранжування → **товстий aggregator, тонкий scorer**; RAG/LLM-judge → навпаки. Структура
(ролі, типи, concurrency, звіт) — одна. Саме тому `EvalCase.input`/`labels` — `BaseModel`, а не QA-форма.

---

## Походження датасету

Дві окремі речі, які не варто плутати під словом «gold»:

**Розмітка — опціональна й по-метрично.** `EvalCase` несе обов'язковим лише `input`; `labels` —
опціональні. Бо:
- **Reference-free метрики** (faithfulness, answer relevancy, citation *precision*) оцінюють вихід
  проти `sources`, не проти «правильної відповіді» → потребують **лише `input`**, нуль розмітки.
- **Reference-based метрики** (refusal, citation *recall*) — лише вони потребують `labels`.

Тож «dataset loader» не передбачає повністю розмічений набір; case з самим `input` (`labels=None`) — валідний.

**Побудова датасету — окремий upstream-крок, не частина рантайм-пайплайна.** Заради
**відтворюваності** датасет має бути зафіксований на час прогону (інакше метрики «плавають» через
датасет, а не через систему). Це усталений патерн проєкту: retrieval-eval будує gold окремими
`build_query_gold.py` / `build_eval_corpus.py`, а eval споживає готовий артефакт. Два режими побудови
(обидва поза пайплайном): **ручне авторство** питань (+міток) або **LLM-синтез із корпусу** →
**обов'язковий людський рев'ю** (для української — той самий ризик, що й калібрування судді).

**Ядро пайплайна бере `EvalCase[]`, не файл.** `run_eval(cases=...)` приймає список кейсів; dataset
loader — лише один продюсер. Кейси можуть прийти з файлу, синтезу або інлайн (smoke на кількох питаннях).

---

## Типи даних (Pydantic-межі)

`eval_common/models.py` — узагальнена база:

```python
class EvalCase(BaseModel):
    id: str                                # стабільний — для join'у й персистенції
    input: BaseModel                       # вхід, специфічний для евалу (generation: GenerationInput)
    labels: BaseModel | None = None        # еталонна розмітка, специфічна для евалу; None = не розмічено

class EvalRun(BaseModel):
    case: EvalCase
    result: BaseModel | None               # будь-який Pydantic-вихід SUT (generation: AnswerResult); None якщо SUT впав
    latency_s: float
    error: str | None = None               # тип винятку (не повідомлення, не payload)

class ScoreCard(BaseModel):
    scorer: str                            # "faithfulness" | "answer_relevancy" | ...
    score: float | None                    # головний скаляр; None = не застосовано (SUT впав / нерелевантно) — JSON-safe, не nan
    detail: BaseModel | None = None        # під-модель скорера (напр. per-claim вердикти)

class ScoredRun(BaseModel):
    run: EvalRun
    cards: list[ScoreCard]

class EvalMetadata(BaseModel):
    eval_name: str
    created_at: str                        # UTC ISO
    sut_models: dict[str, str]             # роль → model-id (напр. {"generator": "...", "embedder": "..."})
    judge_id: str | None = None
    prompt_fingerprints: dict[str, str]    # ім'я промпта → sha256
    dataset_path: str | None              # None якщо кейси інлайн/синтезовані
    n_cases: int

class EvalReport[M: BaseModel](BaseModel):  # узагальнена по типу метрик M
    metadata: EvalMetadata
    metrics: M
    runs: list[ScoredRun]
```

**Рішення (напруга typed vs узагальнений):** базові типи узагальнені; **кожен eval визначає свої
сабтайпи** для `input`, `labels`, `Metrics`. `EvalReport[M]` параметризований по `M`; `EvalCase.input`/
`labels` і `EvalRun.result` — `BaseModel` у базі (каркас **не імпортує** domain-моделей консумера;
конкретний тип консумер знає в місці використання). Приклад для generation:

```python
# scripts/generation/ (консумер, не eval_common):
class GenerationInput(BaseModel):
    question: str
class GenerationLabels(BaseModel):
    answerable: bool | None = None          # gold для refusal
    expected_source_ids: list[str] = []     # gold для citation recall
class GenerationMetrics(BaseModel):
    faithfulness_mean: float
    hallucination_rate: float
    ...
# → EvalCase(input=GenerationInput(...), labels=GenerationLabels(...) | None)
# → EvalReport[GenerationMetrics]
```

---

## Контракти ролей (сигнатури + поведінка, без тіл)

### Runner — `eval_common/runner.py`

```python
async def run_cases(
    cases: list[EvalCase],
    run_one: Callable[[EvalCase], Awaitable[BaseModel]],
    *,
    concurrency: int = 5,
    min_interval_s: float = 0.0,
) -> list[EvalRun]: ...
```

**Поведінка:** для кожного case кличе `run_one` (SUT-адаптер, що постачає eval — generation: лямбда
довкола `AnswerOrchestrator.answer`, що повертає `AnswerResult`). Міряє `latency_s`. Виняток із `run_one`
**ловиться** → `EvalRun` з `result=None, error=type(exc).__name__` (ізоляція: падіння одного case не
валить прогін). Concurrency через `asyncio.Semaphore`; опційний per-model `min_interval_s` sleep для
strict-rate-limit провайдерів. Runner про конкретний тип SUT-виходу нічого не знає (`BaseModel`).

### Scorer — `eval_common/protocols.py`

```python
class Scorer(Protocol):
    name: str
    async def score(self, run: EvalRun) -> ScoreCard: ...
```

**Поведінка:** один `EvalRun` → один `ScoreCard`. Незалежні, композовні; кілька скорерів на run.
LLM-скорери отримують `Judge` як ін'єкцію залежності в конструкторі (не глобал). На `run.result is None`
скорер повертає `ScoreCard(score=None)`, не кидає.

### Judge — `eval_common/judge.py`

```python
class Judge(Protocol):
    id: str
    async def assess(self, prompt: str, *, system: str) -> JudgeVerdict: ...

class JudgeVerdict(BaseModel):
    ...  # типізований вердикт, НЕ dict — конкретні поля задає eval через сабтайп
```

**Поведінка + гігієна (з дослідження):** реальний `Judge` обгортає `LLMClient` з **temp=0**; промпт
**fingerprint-иться** (sha256) у `EvalMetadata`; де є рубрика з варіантами — **порядок опцій
рандомізується** (~5 перестановок; точна балансна перестановка не дає виграшу понад variance reduction).
`eval_common` постачає **`FakeJudge`** (детермінований вердикт) поряд із Protocol. Конкретні judge-**промпти**
(faithfulness-рубрика тощо) живуть у evalі-консумері, не тут.

### Aggregator — постачає eval

```python
def aggregate(scored: list[ScoredRun]) -> M: ...   # M = Metrics-сабтайп евалу
```

**Поведінка:** чиста детермінована функція per-item картки → корпус-метрики (середні, розподіли,
hallucination-бакети, refusal-матриця). Без IO/LLM. Реалізація — у evalі (метрики специфічні), але
підпис фіксований каркасом.

### Reporter — `eval_common/report.py`

```python
def write_report(report: EvalReport[M], out_dir: Path) -> None: ...
```

**Поведінка:** пише **завжди обидва** — `report.json` (повний: metadata + metrics + усі `runs` per-item)
і `report.md` (людський summary-table). Створює `out_dir` (`scripts/outputs/<eval>_eval/`). metadata-блок
єдиний для всіх евалів. MD-рендер — спільний хелпер (вбиває 3× ручний рендер).

### `run_eval()` — тонкий опціональний хелпер — `eval_common/__init__.py`

```python
async def run_eval[M: BaseModel](
    cases: list[EvalCase],
    run_one: Callable[[EvalCase], Awaitable[BaseModel]],
    scorers: list[Scorer],
    aggregate: Callable[[list[ScoredRun]], M],
    metadata: EvalMetadata,
    out_dir: Path,
    *,
    concurrency: int = 5,
) -> EvalReport[M]: ...
```

**Поведінка:** склеює типовий single-pass випадок: `run_cases` → застосувати scorers (concurrent по
run×scorer) → `aggregate` → `write_report`. Generation юзає це. Багатостадійні евали **не зобов'язані** —
композують роль-функції напряму. Це і є гібрид: зручність без обов'язкового ABC.

---

## Розкладка модулів

```
scripts/eval_common/
  __init__.py        # run_eval() + ре-експорт
  models.py          # EvalCase, EvalRun, ScoreCard, ScoredRun, EvalReport[M], EvalMetadata
  protocols.py       # Scorer (+ TResult typing)
  runner.py          # run_cases()
  judge.py           # Judge Protocol, real Judge (LLMClient+hygiene), FakeJudge, JudgeVerdict base
  clients.py         # build_eval_llm(model_id) + parse + throttle-таблиці (перенос із detection_eval)
  report.py          # write_report() + MD-рендер + metadata helper
  fakes.py           # FakeJudge тощо (поряд із Protocol-ами)
```

Консумер (наприклад майбутній `scripts/generation/`) приносить лише: `dataset.py` (→`EvalCase[]`),
`scorers.py`, `<eval>_metrics.py` (`Metrics`-сабтайп + `aggregate`), `judge_prompts.py`, `<eval>_eval.py` (main).

---

## Обробка помилок

| Ситуація | Поведінка | Де |
|---|---|---|
| SUT кидає на одному case | ловиться → `EvalRun(result=None, error=type)`, прогін триває | Runner |
| `run.result is None` у скорері | `ScoreCard(score=None)`, не raise | Scorer |
| Judge повертає непарсибельний вихід | retry політикою `LLMClient`; вичерпано → вердикт-сентинел + лічильник у metadata | Judge |
| Malformed запис датасету | fail-loud на load (Pydantic ValidationError) — до будь-яких LLM-викликів | dataset loader |
| Порожній датасет | 0 cases → звіт із `n=0` (не падіння) | run_eval |

**Логування** (per [[python-logging]]): per-module `logger`; прогрес кожні N (не per-item INFO);
`logger.exception` у except Runner-а; **не** логувати тексти питань/відповідей/судді як payload.

## Concurrency

Один спільний `run_cases` обслуговує обидві осі: фан-аут SUT-викликів (Runner) і фан-аут judge-викликів
(Scorer-стадія викликає той самий хелпер по run×scorer). Throttle-таблиці (`CONCURRENCY_OVERRIDES`,
`MIN_CALL_INTERVAL_SECONDS`) переносяться в `eval_common/clients.py` — кінець крос-модульної мутації
`detection_eval` через `.setdefault`.

## Тести/фейки

Ports-and-adapters: кожен Protocol має фейк у `eval_common/fakes.py`; жоден тест каркаса не торкається
мережі/БД (конвенція `asyncio_mode=auto`). Тест-стратегія випливає з того, чи роль торкається LLM/мережі
(потребує фейка) чи чиста (тестується напряму):

| Роль | LLM/мережа? | Як тестується |
|---|---|---|
| dataset loader | ні | unit (фікстура-файл) |
| Runner | так (SUT) | через `FakeSUT` (фіксований результат) |
| Scorer (judge/embed) | так | через `FakeJudge` / фейк-ембедер |
| Scorer (refusal тощо) | ні | unit |
| Aggregator | ні | unit (фейкові `ScoreCard`) |
| Reporter | ні (IO) | unit (tmp-dir) |

---

## Свідомо поза скоупом / відкладено

- **Конкретні generation-метрики/скорери/judge-промпти** — окремий дизайн-док (консумер каркаса).
- **Калібрування судді** проти нативного UA-gold — проєктується в `Judge` Protocol, але окремий крок;
  найбільший ризик усього треку (cross-lingual Fleiss ≈0.3 — див. дослідження).
- **Ретрофіт 4 наявних евалів** на каркас — пізніше, коли доведений.
- **Персист `EvalRun[]` на диск + `--stages`** — контракт це **дозволяє** (типи серіалізовні, Reporter
  пише runs), але v1 default — single-pass in-memory; CLI-стадії додаються згодом.
- **Cost/token-трекінг** — не в v1 (наявні евали його теж переважно не мають; verification — виняток).

## Відкриті питання

1. `JudgeVerdict` — узагальнена база з вільним `detail`, чи кожен eval повністю свій сабтайп? (схиляюсь до
   мінімальної бази + сабтайп).
2. Чи `aggregate` лишається звичайною функцією-в-evalі, чи стає членом якогось `MetricSet`-об'єкта
   (поки — функція; ABC лише якщо з'явиться третій консумер з тією ж потребою — rule of three).
3. Розмір/походження мінімального людського UA-gold для калібрування судді (питання generation-доку).

---

## Зв'язок із наявним кодом

- Переюзає `LLMClient`/`EmbeddingClient` ([[litellm]]); не форкає прод-код.
- Успадковує патерн проміжних артефактів від `extraction_quality_eval` (run/score роздільність).
- Дотримується [[python-logging]] і typed-boundaries з `CLAUDE.md`.
- Перший консумер — generation-eval (`POST /answer`), драйвить валідацію контрактів.
