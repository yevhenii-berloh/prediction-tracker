from __future__ import annotations

import logging
import re

from prophet_checker.models.domain import CitationRef, ResolvedAnswer, RetrievedPrediction

logger = logging.getLogger(__name__)

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# Один прохід ловить і маркер у дужках, і голий ідентифікатор у прозі: інакше
# другий прохід зіпсував би вже підставлені номери.
_TOKEN_RE = re.compile(rf"\[\s*(?P<bracketed>{_UUID})\s*\]|(?P<bare>{_UUID})")


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
