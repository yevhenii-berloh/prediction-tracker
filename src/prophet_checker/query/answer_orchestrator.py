from __future__ import annotations

import logging

from prophet_checker.llm import LLMClient
from prophet_checker.llm.prompts import RAG_SYSTEM, build_rag_prompt
from prophet_checker.models.domain import AnswerResult
from prophet_checker.query.orchestrator import QueryOrchestrator

logger = logging.getLogger(__name__)

REFUSAL_NO_DATA = (
    "За наявними даними я не знайшов релевантних прогнозів на цей запит. "
    "Аналіз автоматизований і може містити неточності."
)


class AnswerOrchestrator:
    def __init__(self, query_orchestrator: QueryOrchestrator, llm: LLMClient) -> None:
        self._query_orchestrator = query_orchestrator
        self._llm = llm

    async def answer(self, question: str, limit: int = 10) -> AnswerResult:
        result = await self._query_orchestrator.search(question, limit=limit)
        if not result.results:
            logger.info("answer: no relevant sources, refusing")
            return AnswerResult(query=question, answer=REFUSAL_NO_DATA, sources=[])
        prompt = build_rag_prompt(question, result.results)
        text = await self._llm.complete(prompt, system=RAG_SYSTEM)
        logger.info("answer: generated from %d sources", len(result.results))
        return AnswerResult(query=question, answer=text.strip(), sources=result.results)
