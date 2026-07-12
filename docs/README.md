# `docs/` — index

Документи згруповані за use-case. Кожна підпапка — один логічний контур (фіча / задача / процес).

## 📋 Project tracking

| Документ | Призначення |
|----------|-------------|
| [`../progress.md`](../progress.md) | Project-wide progress log (час, вартість, milestones) — перенесено у корінь репо |

## 🏛 [`architecture/`](architecture/) — architectural specs

Що ми будуємо і чому. Index + 7 окремих data flow docs (по аналогії з verifier-v2/).

### Index

| Документ | Призначення |
|----------|-------------|
| [`2026-04-26-architecture-current.md`](architecture/2026-04-26-architecture-current.md) | **Index** — module inventory, table-of-contents 7 flows, shared components, what's next |

### Active data flows (Mermaid діаграми, кожен ~50-100 рядків)

| Документ | Status | Що показує |
|----------|--------|-----------|
| [`flow-1-telegram-collection.md`](architecture/2026-04-26-flow-1-telegram-collection.md) | ✅ implemented | Telethon → JSON-дамп |
| [`flow-2-gold-annotation.md`](architecture/2026-04-26-flow-2-gold-annotation.md) | ✅ implemented | Manual YES/NO розмітка → gold_labels.json |
| [`flow-3-detection-eval.md`](architecture/2026-04-26-flow-3-detection-eval.md) | ✅ Task 13 done | 5 моделей × 2 prompt → P/R/F1 матриця |
| [`flow-4-extraction-quality-eval.md`](architecture/2026-04-26-flow-4-extraction-quality-eval.md) | ✅ Task 13.5 done | 3-stage LLM-as-judge eval |
| [`idle-components.md`](architecture/2026-04-26-idle-components.md) | 🚧 built, not orchestrated | Class inventory of `src/` |
| [`flow-production-ingestion.md`](architecture/2026-04-26-flow-production-ingestion.md) | 📋 Task 15 | Scheduler → collect → detect → extract → save |
| [`flow-production-rag.md`](architecture/2026-04-26-flow-production-rag.md) | 📋 bot module | User query → embed → search → LLM answer |

## 🔬 [`extraction-quality-eval/`](extraction-quality-eval/) — Task 13.5

Вимірювання якості claim extraction (LLM-as-judge). Closeout: Pro Preview виграв за precision (avg 2.30), Flash Lite залишився production вибором (recall 73%, 33× дешевше).

| Документ | Призначення |
|----------|-------------|
| [`2026-04-21-extraction-quality-eval-design.md`](extraction-quality-eval/2026-04-21-extraction-quality-eval-design.md) | Spec: 3-stage LLM-as-judge eval, 6-value verdict, gold-blind judge prompt |
| [`2026-04-21-extraction-quality-eval-plan.md`](extraction-quality-eval/2026-04-21-extraction-quality-eval-plan.md) | Implementation plan — 10 tasks, ~28 TDD tests |
| [`2026-04-26-extraction-consolidated-report.md`](extraction-quality-eval/2026-04-26-extraction-consolidated-report.md) | Per-post per-model report з вердиктами Opus (15 YES + 8 NO постів) |
| [`2026-04-26-gemini-pro-vs-lite-cost.md`](extraction-quality-eval/2026-04-26-gemini-pro-vs-lite-cost.md) | Cost comparison: Pro Preview $115 vs Flash Lite $3.50 на 5572 постах. Two-tier strategy hypothesis. |

## 🔮 [`verifier-v2/`](verifier-v2/) — verification trigger policy

Smart Verifier з Dumb Trigger: 4-status output (confirmed/refuted/unresolved/premature), prediction_strength, max_horizon, retry-loop semantics. Розв'язує проблему target_date=null у 70-90% claims.

### Spec + Plan

| Документ | Призначення |
|----------|-------------|
| [`2026-04-26-verification-trigger-policy-design.md`](verifier-v2/2026-04-26-verification-trigger-policy-design.md) | Spec: full design + state machine + edge cases |
| [`2026-04-29-verification-trigger-policy-plan.md`](verifier-v2/2026-04-29-verification-trigger-policy-plan.md) | Implementation plan — 9 TDD tasks, 58 steps, ~30 tests + empirical re-run |

### Data flow diagrams (Mermaid, by topic — кожен ~50-70 рядків)

| Документ | Що показує |
|----------|-----------|
| [`2026-04-29-verifier-v2-call.md`](verifier-v2/2026-04-29-verifier-v2-call.md) | Single `verify_v2()` call — happy path + failure modes |
| [`2026-04-29-prediction-lifecycle.md`](verifier-v2/2026-04-29-prediction-lifecycle.md) | State machine: Extracted → InFlight/Terminal → ForcedTerminal |
| [`2026-04-29-verification-cycle.md`](verifier-v2/2026-04-29-verification-cycle.md) | Orchestrator cycle: housekeeping → fetch → verify loop |

## 📝 [`annotation/`](annotation/) — Task 12 manual gold labeling

| Документ | Призначення |
|----------|-------------|
| [`annotation-guidelines.md`](annotation/annotation-guidelines.md) | Rubric: YES/NO criteria, anti-patterns. Використовується для gold_labels.json і extraction-eval judge prompt. |

## 🚀 [`aws-deploy/`](aws-deploy/) — мінімальний деплой на AWS (EC2 + Docker Compose, SSH-only)

| Документ | Призначення |
|----------|-------------|
| [`README.md`](aws-deploy/README.md) | Index: статус, документи, артефакти, відхилення від плану |

## 🔎 [`hybrid-retrieval/`](hybrid-retrieval/) — hybrid structured+vector retrieval (Частина B)

Self-querying LLM-планер + typed фільтри (автор + дві дати) поверх exact-скану pgvector. Розв'язує 4 retrieval-проблеми: ім'я автора, дата-коли-сказано, дата-прогнозу, слабка entity-дискримінація embedding.

| Документ | Призначення |
|----------|-------------|
| [`2026-07-11-hybrid-retrieval-design.md`](hybrid-retrieval/2026-07-11-hybrid-retrieval-design.md) | Design: QueryPlanner + SearchFilters, `WHERE` на exact-скані, null-inclusive target_date, fail-fast, REFUSAL_UNKNOWN_AUTHOR (рішення Р1–Р7) |
| [`2026-07-11-hybrid-retrieval-plan.md`](hybrid-retrieval/2026-07-11-hybrid-retrieval-plan.md) | Implementation plan — 9 tasks, TDD, квіз самоперевірки в кожному таску |

## 🤖 [`telegram-bot/`](telegram-bot/) — Telegram-бот (user-facing Q&A)

Остання миля продукту: тонкий фронтенд над `AnswerOrchestrator` — aiogram,
long-polling у процесі API. Stateless, author-agnostic, публічний без лімітів.

| Документ | Призначення |
|----------|-------------|
| [`2026-07-11-telegram-bot-design.md`](telegram-bot/2026-07-11-telegram-bot-design.md) | Spec: рішення й чому, компоненти, UX-таблиця, помилки |
| [`2026-07-11-telegram-bot-plan.md`](telegram-bot/2026-07-11-telegram-bot-plan.md) | Implementation plan — 7 тасків TDD |

---

## Чому ця структура

Документи про одну фічу/задачу часто пишуться парою (`design.md` + `plan.md`) і доповнюються артефактами (cost-comparison, data-flows, reports). Тримати їх у одній subdir дозволяє:
- одразу бачити повний контекст use-case
- спрощує навігацію (12 файлів flat — складно сканувати)
- очевидне місце для нових артефактів — додаючи новий cost analysis для extraction, кладемо в `extraction-quality-eval/` без роздумів

## Conventions

- Імена файлів: `YYYY-MM-DD-<topic>.md` — дата творення (не оновлення).
- Cross-references використовують relative paths.
- Master plan і architecture-current — **living documents**, оновлюються з кожним milestone.
- `progress.md` — у корені репо, project-wide progress log (оновлюється з кожним milestone).
