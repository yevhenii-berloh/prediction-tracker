# RAG Generation Eval — підсумок дослідження

**Дата:** 2026-06-25
**Контекст:** брейншторм стандартизації спільного eval-каркаса prediction-tracker; наступний eval = оцінка генерації (`POST /answer`).
**Метод:** deep-research (5 кутів пошуку → 22 джерела → 110 claims → adversarial-верифікація 25, підтверджено 23, вбито 2; 104 агенти, ~2.95M токенів).
**Knowledge-base версія (Brain wiki):** `concepts/rag-generation-eval.md`, `concepts/llm-as-judge.md`, `concepts/multilingual-llm-judge.md`.

---

## TL;DR

**Будувати тонкий власний eval-харнес, НЕ adopt-ити фреймворк.** Ragas / DeepEval / RAGChecker самі по собі — це LLM-as-judge / NLI калькулятори. Їхня справжня цінність — *визначення метрик* + *структура* (dataset → runner → scorer → reporter), і те, й те інтерналізується **без залежності**. Adopt дав би важку залежність, що дублює наш LiteLLM, повертає magic-dict-и (проти «typed boundaries») і **все одно вимагає калібрування під українську**.

**Вирішальний ризик — не фреймворк, а український суддя.** Калібрувати проти людських міток на нативному UA-gold; temp 0; fingerprint промпта; рандомізувати порядок опцій рубрики.

Ragas лишити як **опціональний разовий крос-чек** (через його LiteLLM-адаптер, без зміни клієнта) — тільки якщо власні метрики виявляться ненадійними.

---

## 1. Канонічні метрики та як їх рахувати

Усі дешево перевизначаються на типізований `AnswerResult{answer, sources}` (кожна підтверджена 3-0):

| Метрика | Формула | Що вже є в репо |
|---|---|---|
| **Faithfulness** (groundedness) | `supported claims / total claims` — декомпозувати відповідь на атомарні твердження → entailment кожного проти sources → частка (Ragas: 1/2 = 0.5) | `judge_prompts.py` робить per-claim вердикти |
| **Answer relevancy** | LLM **зворотно** генерує ~3 питання з відповіді → середній cosine ембедингів vs оригінальне питання (не гарант. 0–1; cosine ∈ −1..1) | `EmbeddingClient` (юзає `retrieval_eval.py`) |
| **Citation / attribution P/R** | NLI-entailment кожного твердження проти cited sources; ALCE Citation-NLI P/R/F1; RAGChecker claim-level: precision = коректні/усі, recall = коректні gold/усі gold | мапиться на `sources` |
| **RAGChecker діагностичний спліт** | некоректні твердження → `relevant-noise` / `irrelevant-noise` / **`hallucination`** + `self-knowledge` + `context-utilization` | відділяє збій генератора від retriever — наш seam (AnswerOrchestrator переюзає search) |

**RAG-тріада** (TruLens/DeepEval): context relevance (retriever) → groundedness/faithfulness (генератор не вигадує) → answer relevance (відповідає на питання). Faithfulness ≈ groundedness.

**Hallucination-бакет — ключова метрика** для grounded-відповідей. Прямо ловить дефект зі smoke-тесту `/answer` (модель вигадала статистику).

---

## 2. LLM-as-judge — best practices

- **Pointwise (rubric)** простіше/дешевше/відтворюваніше за **pairwise** (N², дорожче). Маємо position bias по порядку опцій рубрики.
- **Position bias реальний і модель-специфічний за напрямком** (χ² p<0.05; 6 суддів × 4 датасети) — одні first-biased, інші last-biased. Тому «правильний першим» **хибно**.
- **Мітигація порядку:** рандомізувати ~5 перестановок. **Точна збалансована перестановка** ≈0 виграшу над випадковою (CI містить 0 на 11/12) — виграш суто variance reduction, ~85% за K=5.
- **Verbosity / self-preference** — судді люблять довші відповіді й власний стиль.
- **Гігієна відтворюваності:** temp 0, fingerprint (sha256) промпта в артефакт, фіксувати id моделі-судді. Extraction-quality eval уже хешує промпт.
- **Калібрування проти людей — не пропускати.**

---

## 3. Мультимовна надійність судді (вирішальний ризик)

- Cross-lingual узгодженість низька: середній **Fleiss Kappa ≈ 0.3** (25 мов × 5 моделей, EMNLP 2025); GPT-4o максимум ~0.54.
- Low-resource колапс: Telugu-English Cohen **0.002** на MGSM; fine-tuning і масштаб **не виправляють**.
- **Translationese bias** (30 мов): суддя віддає перевагу машинному перекладу над людським — гірше для low-resource.
- **Українська = mid-resource** (уражена менше) + у проєкті **нативний UA-gold** (гасить translationese, бо bias тригериться MT-референсами) → ризик реальний, але **керований за умови калібрування**.

**Actionable:** калібрувати проти нативного UA-gold (не back-translated); розглянути UA-промпт судді vs англ.; temp0+fingerprint+shuffle; визначити поріг Kappa для довіри.

---

## 4. Фреймворки — що з'ясовано

- **Ragas** (якщо adopt-ити): чисто лягає через **LiteLLM-адаптер** (100+ провайдерів вкл. Gemini), без proprietary-обгортки (`LangchainLLMWrapper` deprecated → `llm_factory` auto-detect); non-English через `adapt()`, але переклад LLM-based і **error-prone, треба ручний рев'ю**.
- **DeepEval** RAG-метрики — обгортка над Ragas (`pip install ragas`; композит-середнє AR/faithfulness/ctx-precision/ctx-recall).
- **Не верифіковано** (per-tool деталі): TruLens, OpenAI Evals, UK AISI Inspect, promptfoo, LangSmith, Arize Phoenix, MLflow LLM evaluate, Braintrust. Найімовірніші «теж пасують»: TruLens, Inspect.

---

## 5. Чесний облік: спростоване й невизначене

**Спростовано adversarial-голосуванням (0-3):**
- «Style-bias (markdown) — домінантне зміщення (0.10–0.76), далеко перевершує position (≤0.04)».
- «Стекінг position-swap + CoT + калібрована рубрика дає +11.5 pp (Claude) / +7.5 pp (Flash)».

**Caveats:**
- Ragas/DeepEval API швидко еволюціонують (`llm_factory`/LiteLLM — поточний v0.4, червень 2026) — перевіряти на момент імплементації.
- ≈0.3 Fleiss — cross-model середнє, не UA-/SOTA-специфічне.
- Європейські 0.30–0.61 Cohen Kappa — з **XQuAD**, не MGSM (виправлено під час верифікації).
- Точні величини judge-bias невизначені; тримається лише «калібруй проти людей».
- Повне порівняння 8 фреймворків лише частково доказове.

---

## 6. Відкриті питання

1. Per-tool деталі 8 неверифікованих фреймворків (OSS vs platform, ліцензія, вага залежності, чи обгортає in-house `answer+sources` функцію).
2. Мінімальний розмір людського UA-gold для калібрування + поріг Kappa для mid-resource мови.
3. Виміряний приріст точності UA-промпт судді vs англомовний над тими ж UA-відповідями.
4. **Citation без маркерів** (`answer+sources`, без [n]→id): чи можна рахувати precision на рівні відповіді (усі твердження проти всього top-k) без over-credit від нерелевантного джерела — чи потрібне marker-binding.

---

## 7. Що це означає для дизайну каркаса

- Структура **dataset → runner → scorer → reporter** — те, що 4 наявні евали вже наближено мають; стандарт її закріплює.
- Новий generation-eval = доказ каркаса; метрики §1 реалізуються поверх наявних `LLMClient`/`EmbeddingClient` (не форкати прод-код).
- Окрема підзадача калібрування судді (§3) — найбільший ризик, планувати явно.
- Форму стандарту (A тонка бібліотека / B Evaluator-фреймворк / C гібрид) — **ще не обрано**.

---

## Джерела (верифіковані)

- Ragas metrics — https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/
- Ragas LiteLLM-адаптер — https://docs.ragas.io/en/stable/howtos/llm-adapters/
- Ragas prompt adaptation — https://docs.ragas.io/en/v0.1.21/howtos/applications/use_prompt_adaptation.html
- DeepEval ↔ Ragas — https://deepeval.com/docs/metrics-ragas
- RAGChecker (NeurIPS 2024) — https://arxiv.org/pdf/2408.08067
- RAG eval survey 2025 — https://arxiv.org/pdf/2508.15396
- Position bias (model-specific) — https://arxiv.org/pdf/2602.02219
- Judge bias-mitigation — https://arxiv.org/html/2604.23178
- Cross-lingual judge consistency (EMNLP 2025) — https://arxiv.org/html/2505.12201v1
- Translationese bias (30 мов) — https://arxiv.org/pdf/2603.10351
