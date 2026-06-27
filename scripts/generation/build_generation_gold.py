# scripts/generation/build_generation_gold.py
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA = PROJECT_ROOT / "scripts" / "data"


def build_gold(
    retrieval_gold: list[dict], manual: list[dict], claim_by_id: dict[str, str]
) -> list[dict]:
    """Pure transform: retrieval-gold + manual questions + corpus claims → gold records."""
    by_target: dict[str, dict[str, str]] = {}
    for e in retrieval_gold:
        by_target.setdefault(e["target_id"], {})[e["source_field"]] = e["query"]

    out: list[dict] = []
    for i, tid in enumerate(sorted(by_target)):
        phr = by_target[tid]
        prefer = "claim_text" if i % 2 == 0 else "situation"
        other = "situation" if prefer == "claim_text" else "claim_text"
        out.append(
            {
                "id": f"a{i:03d}",
                "question": phr.get(prefer) or phr[other],
                "answerable": True,
                "expected_sources": [{"prediction_id": tid, "claim": claim_by_id[tid]}],
                "category": "single_source",
            }
        )

    s = o = 0
    for m in manual:
        answerable = m["category"] == "synthesis"
        if answerable:
            cid, s = f"s{s:03d}", s + 1
            expected = [{"prediction_id": p, "claim": claim_by_id[p]} for p in m["prediction_ids"]]
        else:
            cid, o = f"o{o:03d}", o + 1
            expected = []
        out.append(
            {
                "id": cid,
                "question": m["question"],
                "answerable": answerable,
                "expected_sources": expected,
                "category": m["category"],
            }
        )
    return out


def main() -> None:
    retrieval_gold = json.loads((DATA / "retrieval_query_gold.json").read_text(encoding="utf-8"))
    manual = json.loads((DATA / "generation_manual_questions.json").read_text(encoding="utf-8"))
    corpus = json.loads((DATA / "retrieval_eval_corpus.json").read_text(encoding="utf-8"))
    claim_by_id = {p["id"]: p["claim_text"] for p in corpus}

    gold = build_gold(retrieval_gold, manual, claim_by_id)
    out_path = DATA / "generation_gold.json"
    out_path.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(gold)} cases → {out_path}")


if __name__ == "__main__":
    main()
