from __future__ import annotations

import logging

from prophet_checker.llm import LLMClient
from prophet_checker.llm.prompts import RAG_SYSTEM, build_rag_prompt
from prophet_checker.models.domain import AnswerResult, RetrievedPrediction
from prophet_checker.query.orchestrator import QueryOrchestrator

logger = logging.getLogger(__name__)

REFUSAL_NO_DATA = (
    "За наявними даними я не знайшов релевантних прогнозів на цей запит. "
    "Аналіз автоматизований і може містити неточності."
)

REFUSAL_UNKNOWN_AUTHOR = (
    "У базі немає прогнозів автора «{author}». Аналіз автоматизований і може містити неточності."
)


class AnswerOrchestrator:
    def __init__(self, llm: LLMClient, query_orchestrator: QueryOrchestrator | None = None) -> None:
        self._llm = llm
        self._query_orchestrator = query_orchestrator

    async def answer_from_sources(
        self, question: str, sources: list[RetrievedPrediction]
    ) -> AnswerResult:
        if not sources:
            logger.info("answer_from_sources: no sources, refusing")
            return AnswerResult(query=question, answer=REFUSAL_NO_DATA, sources=[])
        prompt = build_rag_prompt(question, sources)
        text = await self._llm.complete(prompt, system=RAG_SYSTEM)
        logger.info("answer_from_sources: generated from %d sources", len(sources))
        return AnswerResult(query=question, answer=text.strip(), sources=sources)

    async def answer(self, question: str, limit: int = 10) -> AnswerResult:
        if self._query_orchestrator is None:
            raise RuntimeError(
                "answer() requires a query_orchestrator (this instance is generate-only)"
            )
        result = await self._query_orchestrator.search(question, limit=limit)
        if result.unknown_author is not None:
            logger.info("answer: unknown author, refusing")
            return AnswerResult(
                query=question,
                answer=REFUSAL_UNKNOWN_AUTHOR.format(author=result.unknown_author),
                sources=[],
            )
        return await self.answer_from_sources(question, result.results)
