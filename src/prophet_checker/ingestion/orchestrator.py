from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from prophet_checker.ingestion.report import ChannelReport, CycleReport
from prophet_checker.models.domain import PersonSource, SourceType
from prophet_checker.sources.base import Source
from prophet_checker.storage.interfaces import (
    PredictionRepository,
    SourceRepository,
)


class IngestionOrchestrator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        source_repo: SourceRepository,
        prediction_repo: PredictionRepository,
        extractor,
        embedder,
        sources: Mapping[SourceType, Source],
    ) -> None:
        self._session_factory = session_factory
        self._source_repo = source_repo
        self._prediction_repo = prediction_repo
        self._extractor = extractor
        self._embedder = embedder
        self._sources = sources

    async def run_cycle(self, limit: int | None = None) -> CycleReport:
        started_at = datetime.now(UTC)
        active = await self._source_repo.list_active_sources()
        channels: list[ChannelReport] = []
        for ps in active:
            report = await self._process_channel(ps, limit)
            channels.append(report)
        finished_at = datetime.now(UTC)
        return CycleReport(
            started_at=started_at,
            finished_at=finished_at,
            channels_processed=channels,
        )

    async def _process_channel(self, ps: PersonSource, limit: int | None = None) -> ChannelReport:
        report = ChannelReport(
            person_source_id=ps.id,
            cursor_advanced_to=ps.last_collected_at,
        )
        source = self._sources.get(ps.source_type)
        if source is None:
            report.error = f"no source registered for type={ps.source_type.value}"
            return report

        try:
            async for raw_doc in source.collect(ps, since=ps.last_collected_at, limit=limit):
                report.posts_seen += 1
                predictions = await self._extractor.extract(
                    text=raw_doc.raw_text,
                    person_id=raw_doc.person_id,
                    document_id=raw_doc.id,
                    person_name=ps.source_identifier,
                    published_date=raw_doc.published_at.date().isoformat(),
                )
                if predictions:
                    report.posts_with_predictions += 1
                    for p in predictions:
                        p.embedding = await self._embedder.embed(p.claim_text)
                    async with self._session_factory() as session:
                        async with session.begin():
                            await self._source_repo.save_document(raw_doc, session=session)
                            for p in predictions:
                                await self._prediction_repo.save(p, session=session)
                            await self._source_repo.update_source_cursor(
                                ps.id, raw_doc.published_at, session=session
                            )
                    report.predictions_extracted += len(predictions)
                else:
                    async with self._session_factory() as session:
                        async with session.begin():
                            await self._source_repo.update_source_cursor(
                                ps.id, raw_doc.published_at, session=session
                            )
                report.cursor_advanced_to = raw_doc.published_at
        except Exception as exc:
            report.error = f"halted at step=processing: {exc}"
        return report
