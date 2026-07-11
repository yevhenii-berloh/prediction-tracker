from __future__ import annotations

import logging
from datetime import date

from prophet_checker.llm import LLMClient
from prophet_checker.llm.prompts import (
    SELF_QUERY_SYSTEM,
    build_self_query_prompt,
    parse_query_plan,
)
from prophet_checker.models.domain import QueryPlan
from prophet_checker.storage.interfaces import PersonRepository

logger = logging.getLogger(__name__)


class QueryPlanningError(Exception):
    """Планер не побудував валідний план — запит падає (design Р4, fail fast)."""


class QueryPlanner:
    def __init__(self, llm: LLMClient, person_repo: PersonRepository) -> None:
        self._llm = llm
        self._person_repo = person_repo

    async def plan(self, question: str) -> QueryPlan:
        persons = await self._person_repo.list_all()
        prompt = build_self_query_prompt(question, persons, today=date.today())
        try:
            raw = await self._llm.complete(prompt, system=SELF_QUERY_SYSTEM)
            plan = parse_query_plan(raw, {p.id for p in persons}, question)
        except Exception as exc:
            # не логуємо тут — boundary (app.py) робить logger.exception один раз
            raise QueryPlanningError(f"query planning failed: {exc}") from exc
        logger.debug("query plan: filters=%s", plan.filters)
        return plan
