from extraction.eval_v2.eval_models import (
    ModelPostScore,
    PostJudgement,
    PostScore,
    RunHealth,
)
from extraction.eval_v2.health import health_flags, run_health


def _score(post_id, *, failed_models=(), coverage_measurable=True):
    per_model = {}
    for model_id in ("a", "b"):
        per_model[model_id] = ModelPostScore(
            extracted=1,
            valid=1,
            hallucinated=0,
            over_extracted=0,
            covered=1,
            extraction_failed=model_id in failed_models,
        )
    return PostScore(
        post_id=post_id,
        author="Арестович",
        stratum="prefilter",
        reference_size=1,
        per_model=per_model,
        coverage_measurable=coverage_measurable,
    )


def _judgement(post_id, *, judge_errors=0):
    return PostJudgement(
        post_id=post_id, claims=[], verdicts=[], missed=[], judge_errors=judge_errors
    )


def test_health_counts_judge_errors_and_affected_posts():
    scores = [_score("p1"), _score("p2"), _score("p3")]
    judgements = [
        _judgement("p1", judge_errors=2),
        _judgement("p2"),
        _judgement("p3", judge_errors=1),
    ]

    health = run_health(scores, judgements)

    assert health.judge_errors == 3
    assert health.posts_with_judge_errors == 2
    assert health.n_posts == 3


def test_health_counts_extraction_failures_per_model():
    scores = [_score("p1", failed_models=("a",)), _score("p2", failed_models=("a", "b"))]

    health = run_health(scores, [_judgement("p1"), _judgement("p2")])

    assert health.extraction_failures == {"a": 2, "b": 1}


def test_health_counts_unmeasurable_coverage_posts():
    scores = [_score("p1", coverage_measurable=False), _score("p2")]

    health = run_health(scores, [_judgement("p1"), _judgement("p2")])

    assert health.posts_coverage_unmeasurable == 1


def test_clean_run_raises_no_flags():
    health = RunHealth(n_posts=100)

    assert health_flags(health) == []


def test_judge_errors_above_the_share_make_the_run_not_decision_grade():
    health = RunHealth(n_posts=100, judge_errors=20, posts_with_judge_errors=20)

    flags = health_flags(health)

    assert any("decision-grade" in flag for flag in flags)


def test_a_single_hiccup_does_not_flag_a_long_run():
    health = RunHealth(n_posts=100, judge_errors=1, posts_with_judge_errors=1)

    assert health_flags(health) == []


def test_extraction_failures_flag_names_the_model():
    health = RunHealth(n_posts=100, extraction_failures={"deepseek/x": 30})

    flags = health_flags(health)

    assert any("deepseek/x" in flag for flag in flags)


def test_unmeasurable_coverage_share_is_flagged_separately():
    health = RunHealth(n_posts=100, posts_coverage_unmeasurable=40)

    flags = health_flags(health)

    assert any("coverage" in flag for flag in flags)


def test_empty_run_does_not_divide_by_zero():
    assert health_flags(RunHealth(n_posts=0)) == []
