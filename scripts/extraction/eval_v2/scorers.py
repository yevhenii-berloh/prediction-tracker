# scripts/extraction/eval_v2/scorers.py
from __future__ import annotations

from extraction.eval_v2.eval_models import (
    ClaimVerdict,
    ModelPostScore,
    PooledClaim,
    PostInput,
    PostJudgement,
    PostScore,
)
from extraction.eval_v2.judging import reference_size


def _empty_tally() -> dict[str, int]:
    return {"valid": 0, "hallucinated": 0, "over_extracted": 0, "covered": 0, "unmeasured": 0}


def _apply_verdict(row: dict[str, int], verdict: ClaimVerdict) -> None:
    """Один вердикт лягає на одну модель. Вердикт спільний — тому й хвороба v1 не вертається."""
    if not verdict.measured:
        row["unmeasured"] += 1
        return
    if verdict.is_hallucinated:
        row["hallucinated"] += 1
    if verdict.is_over_extraction:
        row["over_extracted"] += 1
    if verdict.is_valid:
        row["valid"] += 1
        row["covered"] += 1


def _project(
    judgement: PostJudgement, claims: dict[str, PooledClaim], tally: dict[str, dict[str, int]]
) -> None:
    for verdict in judgement.verdicts:
        claim = claims.get(verdict.claim_id)
        if claim is None:
            continue
        for model_id in claim.models:
            row = tally.get(model_id)
            if row is None:
                continue
            _apply_verdict(row, verdict)


def score_post(
    judgement: PostJudgement,
    post: PostInput,
    model_ids: list[str],
    extracted_counts: dict[str, int],
) -> PostScore:
    """Вердикт на унікальний claim проєктується на кожну модель, що його дала (крок 6)."""
    tally = {model_id: _empty_tally() for model_id in model_ids}
    claims = {claim.claim_id: claim for claim in judgement.claims}
    _project(judgement, claims, tally)

    per_model = {}
    for model_id in model_ids:
        row = tally[model_id]
        per_model[model_id] = ModelPostScore(
            extracted=extracted_counts.get(model_id, 0),
            valid=row["valid"],
            hallucinated=row["hallucinated"],
            over_extracted=row["over_extracted"],
            covered=row["covered"],
            unmeasured=row["unmeasured"],
        )

    return PostScore(
        post_id=post.post_id,
        author=post.author,
        stratum=post.stratum,
        reference_size=reference_size(judgement),
        per_model=per_model,
    )
