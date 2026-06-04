from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from prophet_checker.models.db import PersonDB, PersonSourceDB

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


async def ensure_person_source(session_factory: async_sessionmaker, channel: str) -> str:
    person_id = f"person:{channel}"
    ps_id = f"ps:{channel}"
    async with session_factory() as session:
        existing = await session.execute(
            select(PersonSourceDB).where(PersonSourceDB.id == ps_id)
        )
        if existing.scalar_one_or_none() is None:
            session.add(PersonDB(id=person_id, name=channel))
            session.add(
                PersonSourceDB(
                    id=ps_id,
                    person_id=person_id,
                    source_type="telegram",
                    source_identifier=channel,
                    enabled=True,
                    last_collected_at=EPOCH,
                )
            )
            await session.commit()
    return person_id
