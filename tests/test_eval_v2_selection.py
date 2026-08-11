from extraction.eval_v2.eval_models import ModelMetrics
from extraction.eval_v2.selection import select


def _metrics(
    model_id,
    *,
    hallucination=0.0,
    precision=0.5,
    coverage=0.5,
    cost=0.001,
    determinism=1.0,
):
    return ModelMetrics(
        model_id=model_id,
        n_posts=100,
        hallucination_rate=hallucination,
        precision=precision,
        coverage=coverage,
        over_extraction_rate=0.1,
        determinism=determinism,
        cost_per_post=cost,
    )


def test_hallucination_gate_cuts_even_the_widest_coverage():
    per_model = {
        "base": _metrics("base"),
        "loud": _metrics("loud", hallucination=0.3, coverage=0.9),
    }

    result = select(per_model, "base", noise_band=0.02)

    assert result.winner == "base"


def test_precision_gate_is_relative_to_the_baseline():
    """Антигеймінг: розкидування claims не має вигравати покриття."""
    per_model = {
        "base": _metrics("base", precision=0.51),
        "sloppy": _metrics("sloppy", precision=0.30, coverage=0.99),
    }

    result = select(per_model, "base", noise_band=0.02)

    assert result.winner == "base"


def test_coverage_decides_when_it_separates():
    per_model = {
        "base": _metrics("base", coverage=0.50),
        "better": _metrics("better", coverage=0.70),
    }

    result = select(per_model, "base", noise_band=0.02)

    assert result.winner == "better"
    assert result.decided_by == "coverage"


def test_precision_decides_inside_the_coverage_noise_band():
    per_model = {
        "base": _metrics("base", coverage=0.50, precision=0.50),
        "tie": _metrics("tie", coverage=0.51, precision=0.70),
    }

    result = select(per_model, "base", noise_band=0.05)

    assert result.winner == "tie"
    assert result.decided_by == "precision"


def test_cost_decides_when_coverage_and_precision_are_both_in_the_band():
    per_model = {
        "base": _metrics("base", coverage=0.50, precision=0.50, cost=0.01),
        "cheap": _metrics("cheap", coverage=0.51, precision=0.51, cost=0.001),
    }

    result = select(per_model, "base", noise_band=0.05)

    assert result.winner == "cheap"
    assert result.decided_by == "cost_per_post"


def test_low_determinism_flags_but_does_not_cut():
    per_model = {
        "base": _metrics("base", coverage=0.50),
        "jittery": _metrics("jittery", coverage=0.80, determinism=0.6),
    }

    result = select(per_model, "base", noise_band=0.02)

    assert result.winner == "jittery"
    assert any("determinism" in flag for flag in result.flags)


def test_baseline_alone_survives_when_everyone_else_fails_a_gate():
    per_model = {
        "base": _metrics("base"),
        "bad1": _metrics("bad1", hallucination=0.5),
        "bad2": _metrics("bad2", precision=0.1),
    }

    result = select(per_model, "base", noise_band=0.02)

    assert result.winner == "base"
    assert result.decided_by == "coverage"


def test_no_survivor_returns_no_winner_and_says_so():
    per_model = {"base": _metrics("base", hallucination=0.9)}

    result = select(per_model, "base", noise_band=0.02)

    assert result.winner is None
    assert result.flags


def test_missing_metric_cannot_pass_a_gate():
    """None ≠ добре: модель без precision не проходить, а не проходить мовчки."""
    per_model = {
        "base": _metrics("base"),
        "unknown": _metrics("unknown", coverage=0.99),
    }
    per_model["unknown"].precision = None

    result = select(per_model, "base", noise_band=0.02)

    assert result.winner == "base"
