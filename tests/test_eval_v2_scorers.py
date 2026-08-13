from extraction.eval_v2.eval_models import (
    ClaimVerdict,
    MissedClaim,
    PooledClaim,
    PostInput,
    PostJudgement,
)
from extraction.eval_v2.scorers import score_post

_POST = PostInput(
    post_id="p1",
    author="Арестович",
    channel="@c",
    text="текст",
    published_date="2026-06-01",
    stratum="prefilter",
)


def _verdict(claim_id: str, *, grounded: bool = True, rubric: bool = True) -> ClaimVerdict:
    return ClaimVerdict(
        claim_id=claim_id,
        claim_grounded=grounded,
        situation_grounded=grounded,
        passes_rubric=rubric,
    )


def test_shared_claim_gives_both_models_the_same_verdict():
    """Пряма regression-перевірка на хворобу v1: та сама фраза — той самий вердикт."""
    judgement = PostJudgement(
        post_id="p1",
        claims=[PooledClaim(claim_id="p1:0", claim_text="c", models=["a", "b"])],
        verdicts=[_verdict("p1:0")],
        missed=[],
    )

    score = score_post(judgement, _POST, ["a", "b"], {"a": 1, "b": 1})

    assert score.per_model["a"].valid == score.per_model["b"].valid == 1
    assert score.per_model["a"].covered == score.per_model["b"].covered == 1


def test_hallucination_and_over_extraction_are_different_buckets():
    judgement = PostJudgement(
        post_id="p1",
        claims=[
            PooledClaim(claim_id="p1:0", claim_text="c0", models=["a"]),
            PooledClaim(claim_id="p1:1", claim_text="c1", models=["a"]),
        ],
        verdicts=[_verdict("p1:0", grounded=False), _verdict("p1:1", rubric=False)],
        missed=[],
    )

    score = score_post(judgement, _POST, ["a"], {"a": 2})

    assert score.per_model["a"].hallucinated == 1
    assert score.per_model["a"].over_extracted == 1
    assert score.per_model["a"].valid == 0


def test_reference_set_is_shared_and_silence_is_a_coverage_failure():
    judgement = PostJudgement(
        post_id="p1",
        claims=[PooledClaim(claim_id="p1:0", claim_text="c", models=["a"])],
        verdicts=[_verdict("p1:0")],
        missed=[MissedClaim(text="пропущене")],
    )

    score = score_post(judgement, _POST, ["a", "b"], {"a": 1, "b": 0})

    assert score.reference_size == 2
    assert score.per_model["a"].covered == 1
    assert score.per_model["b"].covered == 0


def test_model_that_did_not_run_gets_a_zero_row_not_a_missing_key():
    judgement = PostJudgement(
        post_id="p1",
        claims=[PooledClaim(claim_id="p1:0", claim_text="c", models=["a"])],
        verdicts=[_verdict("p1:0")],
        missed=[],
    )

    score = score_post(judgement, _POST, ["a", "b"], {"a": 1})

    assert score.per_model["b"].extracted == 0
    assert score.per_model["b"].valid == 0


def test_verdict_for_an_unknown_claim_is_ignored():
    """Суддя вигадав claim_id — рядок не має валити скоринг поста."""
    judgement = PostJudgement(
        post_id="p1",
        claims=[PooledClaim(claim_id="p1:0", claim_text="c", models=["a"])],
        verdicts=[_verdict("p1:0"), _verdict("p1:99")],
        missed=[],
    )

    score = score_post(judgement, _POST, ["a"], {"a": 1})

    assert score.per_model["a"].valid == 1


def test_unmeasured_claim_is_counted_apart_and_scores_nothing():
    judgement = PostJudgement(
        post_id="p1",
        claims=[
            PooledClaim(claim_id="p1:0", claim_text="c0", models=["a"]),
            PooledClaim(claim_id="p1:1", claim_text="c1", models=["a"]),
        ],
        verdicts=[
            _verdict("p1:0"),
            ClaimVerdict(
                claim_id="p1:1",
                claim_grounded=False,
                situation_grounded=False,
                passes_rubric=False,
                measured=False,
            ),
        ],
        missed=[],
    )

    score = score_post(judgement, _POST, ["a"], {"a": 2})

    assert score.per_model["a"].valid == 1
    assert score.per_model["a"].hallucinated == 0  # не суджене ≠ галюцинація
    assert score.per_model["a"].unmeasured == 1


def test_author_and_stratum_travel_with_the_score():
    judgement = PostJudgement(post_id="p1", claims=[], verdicts=[], missed=[])

    score = score_post(judgement, _POST, ["a"], {"a": 0})

    assert score.author == "Арестович"
    assert score.stratum == "prefilter"
    assert score.reference_size == 0
