from __future__ import annotations

import random
from collections import defaultdict
from itertools import cycle


def _cell(row: dict) -> tuple[str, str]:
    """Стратифікаційна клітинка: (topic, рік)."""
    year = str(row["prediction_date"])[:4]
    return (row.get("topic", ""), year)


def sample_targets(corpus: list[dict], n: int, seed: int) -> list[dict]:
    """Round-robin по клітинках (topic, рік) для рівномірного покриття; детерміновано по seed."""
    rng = random.Random(seed)
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in corpus:
        cells[_cell(row)].append(row)
    for key in cells:
        cells[key].sort(key=lambda r: r["id"])
        rng.shuffle(cells[key])
    keys = sorted(cells.keys())
    picked: list[dict] = []
    seen: set[str] = set()
    for key in cycle(keys):
        if len(picked) >= n or len(seen) >= len(corpus):
            break
        bucket = cells[key]
        if bucket:
            row = bucket.pop()
            picked.append(row)
            seen.add(row["id"])
    return picked[:n]
