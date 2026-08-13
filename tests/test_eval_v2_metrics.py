from extraction.eval_v2.eval_models import ModelPostScore, PostScore
from extraction.eval_v2.metrics import METRIC_SPECS, aggregate


def _score(
    post_id,
    *,
    extracted,
    valid,
    covered,
    reference,
    hallucinated=0,
    over_extracted=0,
    unmeasured=0,
    author="Арестович",
    stratum="prefilter",
):
    return PostScore(
        post_id=post_id,
        author=author,
        stratum=stratum,
        reference_size=reference,
        per_model={
            "a": ModelPostScore(
                extracted=extracted,
                valid=valid,
                hallucinated=hallucinated,
                over_extracted=over_extracted,
                unmeasured=unmeasured,
                covered=covered,
            )
        },
    )


def test_precision_is_macro_over_posts():
    scores = [
        _score("p1", extracted=2, valid=1, covered=1, reference=2),
        _score("p2", extracted=4, valid=4, covered=4, reference=4),
    ]

    metrics = aggregate(scores, ["a"])["a"]

    assert metrics.precision == (0.5 + 1.0) / 2  # macro, не 5/6


def test_silent_post_leaves_precision_but_stays_in_coverage():
    scores = [
        _score("p1", extracted=0, valid=0, covered=0, reference=2),
        _score("p2", extracted=2, valid=2, covered=2, reference=2),
    ]

    metrics = aggregate(scores, ["a"])["a"]

    assert metrics.precision == 1.0  # p1 виключено — ділити нема на що
    assert metrics.coverage == 0.5  # p1 лишився: мовчання = провал покриття


def test_empty_reference_set_gives_none_not_zero():
    metrics = aggregate([_score("p1", extracted=0, valid=0, covered=0, reference=0)], ["a"])["a"]

    assert metrics.coverage is None
    assert metrics.precision is None


def test_hallucination_and_over_extraction_share_the_extracted_denominator():
    scores = [
        _score("p1", extracted=4, valid=2, covered=2, reference=2, hallucinated=1, over_extracted=1)
    ]

    metrics = aggregate(scores, ["a"])["a"]

    assert metrics.hallucination_rate == 0.25
    assert metrics.over_extraction_rate == 0.25


def test_unmeasured_claims_leave_the_denominator():
    """4 витягнутих, 2 не суджено → precision рахується з 2, а не з 4."""
    scores = [
        _score("p1", extracted=4, valid=1, covered=1, reference=2, unmeasured=2),
    ]

    metrics = aggregate(scores, ["a"])["a"]

    assert metrics.precision == 0.5


def test_post_where_nothing_was_judged_drops_out_of_precision():
    scores = [
        _score("p1", extracted=3, valid=0, covered=0, reference=0, unmeasured=3),
        _score("p2", extracted=2, valid=2, covered=2, reference=2),
    ]

    metrics = aggregate(scores, ["a"])["a"]

    assert metrics.precision == 1.0  # p1 не тягне середнє вниз
    assert metrics.hallucination_rate == 0.0


def test_slices_by_author_and_stratum():
    scores = [
        _score("p1", extracted=2, valid=2, covered=2, reference=2, author="Арестович"),
        _score("p2", extracted=2, valid=1, covered=1, reference=2, author="Кущ", stratum="random"),
    ]

    metrics = aggregate(scores, ["a"])["a"]

    assert metrics.by_author["Кущ"].n_posts == 1
    assert metrics.by_author["Кущ"].coverage == 0.5
    assert metrics.by_stratum["random"].n_posts == 1
    assert metrics.by_stratum["prefilter"].coverage == 1.0


def test_model_absent_from_every_post_gets_none_metrics():
    metrics = aggregate([_score("p1", extracted=2, valid=2, covered=2, reference=2)], ["a", "b"])

    assert metrics["b"].n_posts == 0
    assert metrics["b"].coverage is None


def test_metric_roles_match_the_design():
    roles = {spec.name: spec for spec in METRIC_SPECS}

    assert roles["hallucination_rate"].gate.blocking is True
    assert roles["precision"].gate.relative_to_baseline is True
    assert roles["precision"].criterion_priority == 2
    assert roles["coverage"].gate is None  # може впорядковувати, не може відсікати
    assert roles["coverage"].criterion_priority == 1
    assert roles["cost_per_post"].criterion_priority == 3
    assert roles["determinism"].gate.blocking is False
    assert roles["over_extraction_rate"].criterion_priority is None  # diagnostic
    assert "prediction_date_drift" not in roles  # знято з набору
