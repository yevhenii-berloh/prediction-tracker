# Prediction Tracker

AI-powered analysis and verification of predictions made by Ukrainian public figures.

## What it does

- Collects public statements from Telegram channels and news sites
- Extracts specific predictions using LLM
- Verifies predictions against real events with confidence scoring
- Provides interactive Telegram bot for querying results (RAG)

## Tech Stack

- Python 3.11+, FastAPI, SQLAlchemy 2.0
- PostgreSQL + pgvector (vector search)
- LiteLLM (provider-agnostic LLM abstraction)
- Docker, AWS (EC2 + RDS)

## Local development

### Prereqs
- Docker Desktop (or compatible runtime) running
- `.venv` created via `pip install -e ".[dev]"`
- `.env` filled (use `.env.example` as template)

### Start
```bash
# 1. Bring up Postgres + pgvector
docker compose up -d
docker logs prophet_postgres   # check "ready to accept connections"

# 2. Apply migrations
.venv/bin/alembic upgrade head

# 3. Start FastAPI
.venv/bin/python -m prophet_checker
```

### Reset DB
```bash
docker compose down -v          # -v drops the pgdata volume
docker compose up -d
.venv/bin/alembic upgrade head
```

### Stop
```bash
docker compose down             # data preserved in pgdata volume
```

## Status

Under development

## License

MIT
