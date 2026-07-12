from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from prophet_checker.config import Settings
from prophet_checker.factory import (
    build_answer_orchestrator,
    build_bot,
    build_orchestrator,
    build_query_orchestrator,
)
from prophet_checker.ingestion import CycleReport
from prophet_checker.models.domain import AnswerResult, QueryResult

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    async with AsyncExitStack() as stack:
        orchestrator = await build_orchestrator(settings, stack)
        app.state.orchestrator = orchestrator
        app.state.query_orchestrator = await build_query_orchestrator(settings, stack)
        app.state.answer_orchestrator = await build_answer_orchestrator(settings, stack)
        bot_runner = await build_bot(settings, stack, app.state.answer_orchestrator)
        if bot_runner is not None:
            await bot_runner.start()
        yield


app = FastAPI(title="prediction-tracker", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest/run", response_model=CycleReport)
async def run_ingestion(request: Request) -> CycleReport:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="orchestrator not initialized — server is starting up or shutting down",
        )
    try:
        return await orchestrator.run_cycle()
    except Exception as exc:
        logger.exception("run_cycle failed catastrophically")
        raise HTTPException(
            status_code=500,
            detail=f"unexpected orchestrator failure: {type(exc).__name__}",
        )


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


@app.post("/query", response_model=QueryResult)
async def query(req: QueryRequest, request: Request) -> QueryResult:
    query_orchestrator = getattr(request.app.state, "query_orchestrator", None)
    if query_orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="query orchestrator not initialized — server is starting up or shutting down",
        )
    try:
        return await query_orchestrator.search(req.question, req.limit)
    except Exception as exc:
        logger.exception("query failed")
        raise HTTPException(status_code=500, detail=f"query failure: {type(exc).__name__}")


class AnswerRequest(BaseModel):
    question: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)


@app.post("/answer", response_model=AnswerResult)
async def answer(req: AnswerRequest, request: Request) -> AnswerResult:
    answer_orchestrator = getattr(request.app.state, "answer_orchestrator", None)
    if answer_orchestrator is None:
        raise HTTPException(
            status_code=503,
            detail="answer orchestrator not initialized — server is starting up or shutting down",
        )
    try:
        return await answer_orchestrator.answer(req.question, req.limit)
    except Exception as exc:
        logger.exception("answer failed")
        raise HTTPException(status_code=500, detail=f"answer failure: {type(exc).__name__}")
