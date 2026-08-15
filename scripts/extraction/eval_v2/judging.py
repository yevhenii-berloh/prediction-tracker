# scripts/extraction/eval_v2/judging.py
from __future__ import annotations

import logging

from eval_common.judge import Judge

from extraction.eval_v2.eval_models import (
    ClaimVerdict,
    MissedClaim,
    PooledClaim,
    PoolResult,
    PostJudgement,
)
from extraction.eval_v2.judge_prompts import (
    CHECK_SYSTEM,
    MISSED_SYSTEM,
    build_check_prompt,
    build_missed_prompt,
    parse_check_response,
    parse_missed_response,
)

logger = logging.getLogger(__name__)

# Ловимо Exception, а не три типи: транспортний збій (rate limit, таймаут) має
# деградувати так само, як нерозбірлива відповідь — сентинелом на claim, а не
# втратою всього поста. CancelledError успадковує BaseException і проходить далі.
_JUDGE_FAILURES = Exception


_PARSE_FAILURES = (ValueError, KeyError, TypeError)


def _sentinel(claim_id: str, reason: str) -> ClaimVerdict:
    """Суддя не відповів → claim не суджено: ні валідний, ні галюцинація.

    Причина розрізняє «відповів, але сміттям» і «не відповів узагалі» — лікуються
    вони по-різному: перше промптом, друге ретраями чи паузою.
    """
    return ClaimVerdict(
        claim_id=claim_id,
        claim_grounded=False,
        situation_grounded=False,
        passes_rubric=False,
        reason=reason,
        measured=False,
    )


async def _check_claim(
    post_text: str, claim: PooledClaim, judge: Judge
) -> tuple[ClaimVerdict, int]:
    prompt = build_check_prompt(post_text, claim.claim_text, claim.situation)
    try:
        raw = await judge.assess(prompt, system=CHECK_SYSTEM)
        return parse_check_response(raw, claim.claim_id), 0
    except _PARSE_FAILURES:
        logger.exception("перевірний вердикт не розібрався: claim=%s", claim.claim_id)
        return _sentinel(claim.claim_id, "judge-unparsable"), 1
    except _JUDGE_FAILURES:
        logger.exception("суддя недоступний: claim=%s", claim.claim_id)
        return _sentinel(claim.claim_id, "judge-unavailable"), 1


async def _ask_missed(
    post_text: str, claims: list[PooledClaim], judge: Judge
) -> tuple[list[MissedClaim], int]:
    prompt = build_missed_prompt(post_text, [claim.claim_text for claim in claims])
    try:
        raw = await judge.assess(prompt, system=MISSED_SYSTEM)
        return parse_missed_response(raw), 0
    except _JUDGE_FAILURES:
        logger.exception("missed-виклик не розібрався")
        return [], 1


async def judge_post(
    post_id: str, post_text: str, pooled: PoolResult, judge: Judge
) -> PostJudgement:
    """Три перевірні питання на унікальний claim + один виклик на пост про пропущене."""
    claims = pooled.claims
    verdicts: list[ClaimVerdict] = []
    errors = 0
    for claim in claims:
        verdict, failed = await _check_claim(post_text, claim, judge)
        verdicts.append(verdict)
        errors += failed

    missed, missed_failed = await _ask_missed(post_text, claims, judge)
    return PostJudgement(
        post_id=post_id,
        claims=claims,
        verdicts=verdicts,
        missed=missed,
        judge_errors=errors + missed_failed,
        clustering_failed=pooled.clustering_failed,
        missed_measured=missed_failed == 0,
    )


def reference_size(judgement: PostJudgement) -> int:
    """Унікальні валідні claims усіх учасників + пропущене, назване суддею (крок 5)."""
    valid = 0
    for verdict in judgement.verdicts:
        if verdict.is_valid:
            valid += 1
    return valid + len(judgement.missed)
