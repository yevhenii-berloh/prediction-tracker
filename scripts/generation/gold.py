# scripts/generation/gold.py
from __future__ import annotations

import json
from pathlib import Path

from eval_common.models import EvalCase
from generation.gen_models import ExpectedSource, GenerationInput, GenerationLabels


def load_generation_gold(path: Path) -> list[EvalCase]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = []
    for r in data:
        labels = GenerationLabels(
            answerable=r["answerable"],
            expected_sources=[ExpectedSource(**es) for es in r["expected_sources"]],
            category=r["category"],
        )
        cases.append(
            EvalCase(id=r["id"], input=GenerationInput(question=r["question"]), labels=labels)
        )
    return cases
