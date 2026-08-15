from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Mapping

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from prophet_checker.analysis.embedding_text import embedding_text
from prophet_checker.ingestion.report import ChannelReport, CycleReport
from prophet_checker.models.domain import PersonSource, Prediction, RawDocument, SourceType
from prophet_checker.sources.base import Source
from prophet_checker.storage.interfaces import (
    PredictionRepository,
    SourceRepository,
)

logger = logging.getLogger(__name__)


class IngestionOrchestrator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        source_repo: SourceRepository,
        prediction_repo: PredictionRepository,
        extractor,
        embedder,
        sources: Mapping[SourceType, Source],
        log_every: int = 5,
    ) -> None:
        self._session_factory = session_factory
        self._source_repo = source_repo
        self._prediction_repo = prediction_repo
        self._extractor = extractor
        self._embedder = embedder
        self._sources = sources
        self._log_every = log_every

    async def run_cycle(self, limit: int | None = None) -> CycleReport:
        started_at = datetime.now(UTC)
        active = await self._source_repo.list_active_sources()
        channels: list[ChannelReport] = []
        for ps in active:
            # print(f"Running ingestion cycle on source: {ps.id}/{ps.last_collected_at}/{ps.source_type}")
            report = await self._process_channel(ps, limit)
            channels.append(report)
        finished_at = datetime.now(UTC)
        return CycleReport(
            started_at=started_at,
            finished_at=finished_at,
            channels_processed=channels,
        )

    async def _process_post(
        self, ps: PersonSource, raw_doc: RawDocument, report: ChannelReport
    ) -> bool:
        """Обробити один пост. False = зупинити канал, не рухаючи курсор.

        Курсор — це позначка часу, і рухається він per-post. Тому «просто не рухати
        його на збої» не працює: наступний успішний пост має пізнішу дату й перестрибне
        невдалий. Єдиний спосіб не загубити пост — спинити канал саме на ньому.
        """
        outcome = await self._extractor.extract(
            text=raw_doc.raw_text,
            person_id=raw_doc.person_id,
            document_id=raw_doc.id,
            person_name=ps.source_identifier,
            published_date=raw_doc.published_at.date().isoformat(),
        )
        if outcome.failed:
            report.posts_failed += 1
            report.error = f"halted at post={raw_doc.id}: extraction failed ({outcome.error})"
            logger.warning("ingestion %s: екстракція впала на %s", ps.id, raw_doc.id)
            return False

        predictions = outcome.predictions
        if predictions:
            report.posts_with_predictions += 1
            await self._embed_all(predictions)
            await self._save_post(ps, raw_doc, predictions)
            report.predictions_extracted += len(predictions)
        else:
            await self._advance_cursor(ps, raw_doc)
        report.cursor_advanced_to = raw_doc.published_at
        return True

    async def _embed_all(self, predictions: list[Prediction]) -> None:
        if self._embedder is None:
            return
        for p in predictions:
            p.embedding = await self._embedder.embed(embedding_text(p))

    async def _save_post(
        self, ps: PersonSource, raw_doc: RawDocument, predictions: list[Prediction]
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await self._source_repo.save_document(raw_doc, session=session)
                for p in predictions:
                    await self._prediction_repo.save(p, session=session)
                await self._source_repo.update_source_cursor(
                    ps.id, raw_doc.published_at, session=session
                )

    async def _advance_cursor(self, ps: PersonSource, raw_doc: RawDocument) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await self._source_repo.update_source_cursor(
                    ps.id, raw_doc.published_at, session=session
                )

    def _log_progress(self, ps: PersonSource, report: ChannelReport) -> None:
        if report.posts_seen % self._log_every:
            return
        logger.info(
            "ingestion %s: seen=%d with_predictions=%d extracted=%d failed=%d",
            ps.id,
            report.posts_seen,
            report.posts_with_predictions,
            report.predictions_extracted,
            report.posts_failed,
        )

    async def _process_channel(self, ps: PersonSource, limit: int | None = None) -> ChannelReport:
        report = ChannelReport(
            person_source_id=ps.id,
            cursor_advanced_to=ps.last_collected_at,
        )
        source = self._sources.get(ps.source_type)
        print(f"Running ingestion cycle on source: {ps.id}/{ps.person_id}")
        if source is None:
            report.error = f"no source registered for type={ps.source_type.value}"
            return report

        try:
            async for raw_doc in source.collect(ps, since=ps.last_collected_at, limit=limit):
                report.posts_seen += 1
                processed = await self._process_post(ps, raw_doc, report)
                if not processed:
                    break  # курсор лишається на місці — пост повернеться наступним циклом
                self._log_progress(ps, report)
        except Exception as exc:
            report.error = f"halted at step=processing: {exc}"
        logger.info(
            "ingestion %s done: seen=%d with_predictions=%d extracted=%d error=%s",
            ps.id,
            report.posts_seen,
            report.posts_with_predictions,
            report.predictions_extracted,
            report.error or "-",
        )
        return report
