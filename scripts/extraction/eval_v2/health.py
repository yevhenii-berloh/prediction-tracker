# scripts/extraction/eval_v2/health.py
"""Скільки прогону зламалось — і чи можна на його числа спиратись.

Кроки A–B зробили так, що збої нікого не карають: непорахований claim просто
виходить зі знаменника. Побічний ефект — зламаний прогін тепер виглядає точно
як чистий, лише з меншою вибіркою. Тому кількість збоїв має бути числом у
звіті, а прогін із великою їх часткою — прямо позначений як недостатній
для рішення.
"""

from __future__ import annotations

from extraction.eval_v2.eval_models import PostJudgement, PostScore, RunHealth

# Вище цієї частки постів прогін перестає бути підставою для вибору моделі
NOT_DECISION_GRADE_SHARE = 0.05
# Coverage — criterion 1; якщо він недостовірний на такій частці, вибір втрачає опору
COVERAGE_UNMEASURABLE_SHARE = 0.20


def _judge_totals(judgements: list[PostJudgement]) -> tuple[int, int]:
    errors = 0
    affected_posts = 0
    for judgement in judgements:
        errors += judgement.judge_errors
        if judgement.judge_errors:
            affected_posts += 1
    return errors, affected_posts


def _extraction_failures(scores: list[PostScore]) -> dict[str, int]:
    failures: dict[str, int] = {}
    for score in scores:
        for model_id, row in score.per_model.items():
            if row.extraction_failed:
                failures[model_id] = failures.get(model_id, 0) + 1
    return failures


def run_health(scores: list[PostScore], judgements: list[PostJudgement]) -> RunHealth:
    """Порахувати збої прогону. Чисто арифметика — жодних LLM-викликів."""
    judge_errors, posts_with_judge_errors = _judge_totals(judgements)
    unmeasurable = 0
    for score in scores:
        if not score.coverage_measurable:
            unmeasurable += 1

    return RunHealth(
        n_posts=len(scores),
        judge_errors=judge_errors,
        posts_with_judge_errors=posts_with_judge_errors,
        posts_coverage_unmeasurable=unmeasurable,
        extraction_failures=_extraction_failures(scores),
    )


def _share(count: int, total: int) -> float:
    return count / total if total else 0.0


def health_flags(health: RunHealth) -> list[str]:
    """Прапорці, які мають з'явитись у звіті поруч із winner."""
    flags: list[str] = []
    total = health.n_posts

    judge_share = _share(health.posts_with_judge_errors, total)
    if judge_share > NOT_DECISION_GRADE_SHARE:
        flags.append(
            f"суддя збоїв на {judge_share:.0%} постів ({health.posts_with_judge_errors}/{total}) "
            f"— результат не decision-grade"
        )

    coverage_share = _share(health.posts_coverage_unmeasurable, total)
    if coverage_share > COVERAGE_UNMEASURABLE_SHARE:
        flags.append(
            f"coverage недостовірний на {coverage_share:.0%} постів — criterion 1 без опори"
        )

    for model_id, failed in health.extraction_failures.items():
        if _share(failed, total) > NOT_DECISION_GRADE_SHARE:
            flags.append(
                f"екстракція {model_id} впала на {failed}/{total} постів — числа моделі неповні"
            )
    return flags
