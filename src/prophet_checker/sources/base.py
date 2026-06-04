from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol, runtime_checkable

from prophet_checker.models.domain import PersonSource, RawDocument


@runtime_checkable
class Source(Protocol):
    def collect(
        self,
        person_source: PersonSource,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[RawDocument]:
        ...
