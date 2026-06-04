from __future__ import annotations

from datetime import UTC, datetime
from typing import AsyncIterator

from prophet_checker.models.domain import PersonSource, RawDocument


class MockSource:
    def __init__(self, documents: list[RawDocument]):
        self._documents = documents

    async def collect(
        self,
        person_source: PersonSource,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[RawDocument]:
        cutoff = since or datetime.min.replace(tzinfo=UTC)
        count = 0
        for doc in self._documents:
            if doc.person_id == person_source.person_id and doc.published_at > cutoff:
                if limit is not None and count >= limit:
                    return
                yield doc
                count += 1
