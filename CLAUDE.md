# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`prophet-checker` — AI-powered analysis and verification of predictions made by Ukrainian public figures. Pipeline: **collect** public statements (Telegram channels, news) → **extract** specific predictions with an LLM → **verify** them against real events with confidence scoring → serve answers via a RAG Telegram bot. The installed package is `prophet_checker`; the repo/product name is "prediction-tracker".

Current state: pre-deployment. The ingestion pipeline and the FastAPI HTTP trigger (`POST /ingest/run`) work end-to-end. The verifier is the most actively-iterated area. The Telegram bot and RAG query endpoint are designed but not yet built.

## Commands

All commands use the project venv at `.venv` (Python 3.14). Source lives under `src/`, installed editable.

```bash
# Setup
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env                       # then fill in API keys / Telegram creds

# Database (Postgres + pgvector via Docker)
docker compose up -d                       # container: prophet_postgres
.venv/bin/alembic upgrade head             # apply migrations
docker compose down -v && docker compose up -d && .venv/bin/alembic upgrade head   # full reset (drops volume)

# Run the API (uvicorn on 127.0.0.1:8000)
.venv/bin/python -m prophet_checker
curl localhost:8000/health
curl -X POST localhost:8000/ingest/run     # trigger one ingestion cycle

# Tests (unit + integration use in-memory fakes — no Docker / network needed)
.venv/bin/python -m pytest tests/ -q                                                            # full suite
.venv/bin/python -m pytest tests/test_ingestion_orchestrator.py -v                              # one file
.venv/bin/python -m pytest tests/test_ingestion_orchestrator.py::test_run_cycle_no_active_sources -v   # one test
.venv/bin/python -m pytest tests/ --cov=prophet_checker --cov-report=term-missing               # coverage

# Lint / format (ruff; line length 100)
.venv/bin/ruff check .
.venv/bin/ruff format .

# New migration after changing models/db.py
.venv/bin/alembic revision --autogenerate -m "description"

# Integration smoke — hits REAL Postgres + Telegram + LLM APIs (~$0.001–0.005/run)
.venv/bin/python scripts/ingestion/integration_smoke.py --channel @arestovich --limit 1
.venv/bin/python scripts/ingestion/integration_smoke.py --channel @arestovich --limit 1 --component gemini   # isolate one stage
```

## Architecture

A **ports-and-adapters** monolith. Three cross-cutting decisions explain most of the layout:

**1. Protocols + adapters, with fakes for tests.** Capabilities are `typing.Protocol` interfaces implemented by swappable adapters:
- `storage/interfaces.py` defines repository Protocols (Person, Source, Prediction, VectorStore); `storage/postgres.py` implements them on SQLAlchemy async.
- `sources/base.py` defines the `Source` Protocol; `sources/telegram.py` is the real Telethon adapter, `sources/mock.py` the test one.

Because of this seam, `tests/fakes.py` provides `FakeSourceRepo`/`FakePredictionRepo`, so the whole suite runs with no Docker and no network. When you add a Protocol, add its fake alongside.

**2. Two model layers, bridged explicitly.** `models/domain.py` holds Pydantic domain models (the language of business logic); `models/db.py` holds SQLAlchemy ORM models (persistence). `domain_to_*_db()` functions convert between them. Keep ORM types out of business logic — pass domain models across layer boundaries.

**3. One composition root.** `factory.py::build_orchestrator` builds the engine, repos, LLM/embedding clients, extractor, and Telegram source, wires them into the `IngestionOrchestrator`, and registers teardown on an `AsyncExitStack`. `app.py` (FastAPI) calls it from its `lifespan` and stores the orchestrator on `app.state`; `__main__.py` is the uvicorn entry. The only live endpoints are `GET /health` and `POST /ingest/run` (which runs `orchestrator.run_cycle()`).

Ingestion-cycle flow: `IngestionOrchestrator.run_cycle()` (`ingestion/orchestrator.py`) iterates active sources → collects posts → `PredictionExtractor` (`analysis/extractor.py`) pulls claims via the LLM → persists through the repos → returns a `CycleReport`/`ChannelReport` (`ingestion/report.py`). `PredictionVerifier` (`analysis/verifier.py`) checks claims against evidence and is the most actively-iterated component (4-status confirmed/refuted/unresolved/premature design — see `docs/verification-track/` and `docs/verifier-v2/`).

**LLM access is provider-agnostic via LiteLLM.** `llm/client.py` (`LLMClient`, completion) and `llm/embedding.py` (`EmbeddingClient`) wrap LiteLLM; prompt templates live in `llm/prompts.py`. Don't import a vendor SDK directly — go through these. Model is chosen via `.env` (`config.py` defaults to `openai/gpt-4o-mini`); evals selected Gemini 3.1 Flash Lite as the production extraction model.

**Eval scripts share production code — do not fork it.** `scripts/` holds the evaluation pipelines (detection benchmark, extraction-quality LLM-as-judge, verification eval). They import the *same* extractor classes and the *same* prompts from `src/` and run them in a different mode. This is deliberate: a separate eval prompt would let "eval says the model is good" diverge from production behavior. Change a prompt or extractor once and both move together. Eval inputs live in `scripts/data/` (organised into per-track subdirs: `raw/`, `extraction/`, `verification/`, `retrieval/`, `generation/`), outputs in `scripts/outputs/`. Scripts are grouped into domain packages — `scripts/{ingestion,extraction,verification}/` (each with `__init__.py`); cross-imports are package-qualified (e.g. `from extraction.detection_eval import ...`).

## Conventions

- **Async-first tests**: `pytest` with `asyncio_mode = "auto"` — write `async def test_...` with no marker. `pythonpath` covers `src`, `scripts`, `tests` (so eval scripts and `tests/fakes.py` import cleanly).
- **Migrations**: any change to `models/db.py` needs an Alembic revision; the schema uses pgvector, so the Postgres container must be up to autogenerate/apply.
- **Config**: everything flows through `config.py` (`pydantic-settings`, reads `.env`). `extra="ignore"` lets eval-only keys (e.g. `ANTHROPIC_API_KEY`) live in `.env` without bloating the schema.
- **Commits**: history uses `type(scope): subject` (conventional commits), written in Ukrainian.
- **Data files**: new files under `scripts/data/` get an ISO **creation-date suffix** — `<name>_YYYY-MM-DD.json` (e.g. `gold_2026-06-29.json`). Versioning is by keeping snapshots, not overwriting. Consumers reference the **explicit dated path** (a path constant / CLI default), updated when a newer version is generated — no auto "latest" resolution. Historical (pre-convention) files are left as-is; don't rename them.
- **Never commit**: `.env` and the Telethon `tg_session*` file (a logged-in account session).

## Coding rules (new code)

Optimise new code for the next reader. A green linter is a floor, not proof of readability — don't trust "it looks clean"; lean on types and the linter.

- **Typed boundaries, not raw dicts**: at parser / LLM / repo boundaries return a Pydantic model (like the `models/domain.py` models), not a `dict` with string keys — magic-key dicts hide the contract and break silently on a typo.
- **One source of truth for enum values**: validate against the domain enums (`PredictionStatus`, `PredictionStrength`, …); never hardcode their string values (`{"confirmed", "refuted", …}`) in a parser or check.
- **Type your Protocol dependencies**: constructor params taking a repo/client/source get their Protocol type — an untyped `prediction_repo` hides the interface a reader (and the type checker) needs.
- **Comment the WHY, never the WHAT**: a one-line comment only where intent isn't inferable (e.g. overwriting a field from a second LLM call). No comments that restate the code.
- **Keep new functions small and flat**: target cyclomatic complexity ≤10, ≤~40 lines, ≤5 args; use guard clauses / early returns instead of deep nesting.
- **No dense comprehensions**: a comprehension with more than one `for` clause (nested iteration), or one packing nesting + a filter + a transform so it doesn't read at a glance, → expand into explicit `for` loops with guard clauses (or a named helper). A single `for` with one simple `if` is fine. E.g. `{r.distance for run in runs if run.result for r in run.result.results}` → a 4-line loop that accumulates into a set.
- **Flatten nesting — the dominant readability cost.** Cognitive complexity (the *understandability* metric, distinct from cyclomatic) penalises each nesting level **progressively**, so a nested `if`/`for` costs far more than a flat one. Prefer guard clauses / early returns; lift a nested loop or branch into an evocatively-named helper — extracting to a function costs **zero** ("shorthand"), while the nesting it removes was being charged progressively, so the trade is always favourable. Ruff does **not** enforce this (`C901` is cyclomatic) — it's on you, not the linter.
- **Reuse first; rule of three**: use an existing helper before writing new logic; extract a shared helper on the *third* repetition, not the first — don't add speculative abstraction.
- **Edit in place, small diffs**: modify the existing function rather than adding a parallel v2; don't reformat or refactor unrelated code in the same change.
- **Don't unit-test pure Pydantic models**: a class that is only field declarations (no validators, no methods, no computed properties) is tested by Pydantic itself — don't write a test that just constructs it and reads fields back. Test the code that *uses* the model (orchestrators, endpoints, prompts) instead.

## Logging

Use the stdlib `logging` module; loggers are already per-module (`logger = logging.getLogger(__name__)`) — keep it that way. Logging is configured once at the app entry (`__main__.py`), level via `Settings.log_level`.

- **Never `print()` in `src/`** — use `logger`. `print()` is fine only for `scripts/` CLI output.
- **Modules never configure logging** — no `basicConfig`/handlers/`setLevel` at import. Modules only get a logger and log; configuration lives at the entry point.
- **Lazy args, never f-strings in log calls**: `logger.info("verified %d/%d", done, total)`, not `logger.info(f"...")`. Ruff `G004` / pylint `W1203` enforce this.
- **Pick the level deliberately:**

  | Level | Use for |
  |---|---|
  | `DEBUG` | detailed diagnostics, off in prod (payload shapes, per-item traces) |
  | `INFO` | normal milestones: cycle start/finish, counts, model chosen |
  | `WARNING` | unexpected but handled (soft-normalize, retries, skipped items) |
  | `ERROR` | an operation failed — use `logger.exception()` inside an `except` |
  | `CRITICAL` | the process cannot continue |

- **Exceptions**: in `except`, use `logger.exception("…")` (captures the traceback). **Don't log-and-raise** — either log here, or raise and let the boundary (`app.py`) log once. Not both.
- **Never log secrets or raw payloads**: no API keys, Telegram tokens, `.env` values, or full post / LLM request-response bodies. Log IDs, counts, and lengths.
- **Don't over-log in loops**: no per-item `INFO` in the ingestion/verification loops — summarise, or log progress every N (as `run_cycle` already does). Per-item detail → `DEBUG`.
- **Correlation**: include a stable id (e.g. `cycle_id`) on a run's log lines so they grep together. Nice-to-have; don't build a framework for it.

## Design docs are the source of truth (read before building)

This project practices spec-driven development. Non-trivial work is specced as a **`design.md` + `plan.md` pair** inside a use-case subfolder of `docs/` (e.g. `docs/verifier-v2/`, `docs/ingestion-to-aws/`, `docs/verification-track/`), filenamed `YYYY-MM-DD-<topic>.md` by creation date. Before implementing in an area, read that area's design + plan first, and follow the same design → plan → TDD flow for new work.

Living indexes to orient from:
- `docs/README.md` — index of all doc tracks.
- `docs/architecture/2026-04-26-architecture-current.md` — module inventory, the 7 data flows, what's built vs. designed.
- `progress.md` (repo root) — project-wide progress log; per-track status in each `docs/<track>/README.md`.

Most docs are written in Ukrainian. `progress.md` (project root) is the project-wide progress log — time, cost, milestones; `architecture-current.md` holds module/flow detail and the per-track READMEs hold per-task detail.
