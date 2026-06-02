"""Стадія 1/3: вибірка N випадкових постів Арестовича (фільтр за довжиною тексту,
виключення gold-постів) для ручного прогону зв'язки extractor → verifier."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
ALL_POSTS = PROJECT_ROOT / "scripts" / "data" / "arestovich" / "all.json"
GOLD = PROJECT_ROOT / "scripts" / "data" / "verification_gold_labels.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "scripts" / "outputs" / "pipeline_run" / "selected_posts.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--min-chars", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    posts = json.load(open(ALL_POSTS))
    gold = json.load(open(GOLD))
    gold_post_ids = {p["post_id"] for p in gold["predictions"]}

    eligible = [
        p for p in posts
        if p["person_name"] == "Арестович"
        and len(p["text"]) >= args.min_chars
        and p["id"] not in gold_post_ids
    ]

    rng = random.Random(args.seed)
    selected = rng.sample(eligible, min(args.n, len(eligible)))
    selected.sort(key=lambda p: p["published_at"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(selected, f, ensure_ascii=False, indent=2)

    print(f"total Arestovich posts: {len(posts)}")
    print(f"eligible (>= {args.min_chars} chars, not in gold): {len(eligible)}")
    print(f"selected: {len(selected)}  (seed={args.seed})")
    print(f"saved → {args.output}")


if __name__ == "__main__":
    main()
