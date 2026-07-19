from __future__ import annotations

import logging
import re

from prophet_checker.models.domain import (
    Citation,
    CitationRef,
    ResolvedAnswer,
    RetrievedPrediction,
)
from prophet_checker.storage.interfaces import SourceRepository

logger = logging.getLogger(__name__)

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# Один прохід ловить і маркер у дужках, і голий ідентифікатор у прозі: інакше
# другий прохід зіпсував би вже підставлені номери.
_TOKEN_RE = re.compile(rf"\[\s*(?P<bracketed>{_UUID})\s*\]|(?P<bare>{_UUID})")
_MARKER_RE = re.compile(r"\[(\d+)\]")


def resolve(answer: str, sources: list[RetrievedPrediction]) -> ResolvedAnswer:
    """Замінити ідентифікатори прогнозів на порядкові номери за першою появою.

    Усе, що схоже на ідентифікатор і не належить поданим джерелам, вирізається.
    """
    by_id = {s.prediction.id: s.prediction for s in sources}
    marked: list[str] = []
    plain: list[str] = []
    refs: list[CitationRef] = []
    numbers: dict[str, int] = {}
    length = 0
    cursor = 0
    dropped = 0

    for match in _TOKEN_RE.finditer(answer):
        chunk = answer[cursor : match.start()]
        marked.append(chunk)
        plain.append(chunk)
        length += len(chunk)
        cursor = match.end()

        uid = match.group("bracketed")
        if uid is None or uid not in by_id:
            dropped += 1
            continue

        if uid not in numbers:
            numbers[uid] = len(numbers) + 1
        token = f"[{numbers[uid]}]"
        refs.append(
            CitationRef(
                marker=numbers[uid],
                prediction_id=uid,
                document_id=by_id[uid].document_id,
                offset=length,
            )
        )
        marked.append(token)
        length += len(token)

    tail = answer[cursor:]
    marked.append(tail)
    plain.append(tail)

    if dropped:
        logger.warning("citations: вирізано %d маркер(ів), що не резолвляться", dropped)

    return ResolvedAnswer(text="".join(marked), text_unmarked="".join(plain), refs=refs)


def _unique_document_ids(refs: list[CitationRef]) -> list[str]:
    seen: list[str] = []
    for ref in refs:
        if ref.document_id not in seen:
            seen.append(ref.document_id)
    return seen


def _append_ref(citation: Citation, ref: CitationRef) -> None:
    if ref.marker not in citation.markers:
        citation.markers.append(ref.marker)
    if ref.prediction_id not in citation.prediction_ids:
        citation.prediction_ids.append(ref.prediction_id)


async def materialize(refs: list[CitationRef], source_repo: SourceRepository) -> list[Citation]:
    """Зібрати цитати: один пост — одна цитата, скільки б прогнозів з нього не було."""
    if not refs:
        return []

    documents = await source_repo.get_documents_by_ids(_unique_document_ids(refs))
    by_doc = {doc.id: doc for doc in documents}

    grouped: dict[str, Citation] = {}
    order: list[str] = []
    missing = 0

    for ref in refs:
        doc = by_doc.get(ref.document_id)
        if doc is None or not doc.url:
            missing += 1
            continue
        citation = grouped.get(ref.document_id)
        if citation is None:
            citation = Citation(
                markers=[], url=doc.url, published_at=doc.published_at.date(), prediction_ids=[]
            )
            grouped[ref.document_id] = citation
            order.append(ref.document_id)
        _append_ref(citation, ref)

    if missing:
        logger.warning("citations: %d посилан(ня) без придатного URL документа", missing)

    citations = []
    for doc_id in order:
        citations.append(grouped[doc_id])
    return citations


def drop_markers(text: str, keep: set[int]) -> str:
    """Прибрати з тексту маркери, яких нема серед keep."""

    def replace(match: re.Match[str]) -> str:
        return match.group(0) if int(match.group(1)) in keep else ""

    return _MARKER_RE.sub(replace, text)
