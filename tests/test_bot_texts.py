from datetime import date

from prophet_checker.bot.texts import (
    START_TEXT,
    TELEGRAM_MESSAGE_LIMIT,
    compose_answer_message,
    truncate_for_telegram,
)
from prophet_checker.models.domain import Citation

# --- truncate_for_telegram ---


def test_truncate_returns_short_text_unchanged():
    assert truncate_for_telegram("коротка відповідь") == "коротка відповідь"


def test_truncate_keeps_text_at_exact_limit():
    text = "а" * TELEGRAM_MESSAGE_LIMIT
    assert truncate_for_telegram(text) == text


def test_truncate_cuts_overflow_to_limit_with_ellipsis():
    result = truncate_for_telegram("а" * (TELEGRAM_MESSAGE_LIMIT + 1))
    assert len(result) == TELEGRAM_MESSAGE_LIMIT
    assert result.endswith("…")


# --- START_TEXT: guard на обов'язкові елементи (design §5) ---


def test_start_text_has_required_elements():
    assert "Арестович" in START_TEXT  # чий корпус
    assert "автоматизований" in START_TEXT  # дисклеймер
    assert START_TEXT.count("?") >= 2  # приклади питань


# --- блок джерел (Task 8) ---


def _citation(markers: list[int], url: str, day: int = 12) -> Citation:
    return Citation(
        markers=markers, url=url, published_at=date(2020, 8, day), prediction_ids=["x"]
    )


def test_sources_block_groups_markers_of_one_post():
    citations = [_citation([1, 3], "https://t.me/@ch/1")]

    message = compose_answer_message("текст [1] і [3]", citations)

    assert "Джерела:" in message
    assert '<a href="https://t.me/@ch/1">[1][3] Пост від 12.08.2020</a>' in message


def test_no_citations_means_no_block():
    assert compose_answer_message("просто текст", []) == "просто текст"


def test_citation_dropped_when_its_marker_does_not_survive_truncation():
    # довше за ліміт разом із блоком джерел, тож маркер у хвості точно не переживе обрізання
    long_text = "а" * 5000 + " [2]"
    citations = [_citation([2], "https://t.me/@ch/2", 13)]

    message = compose_answer_message(long_text, citations)

    assert "[2] Пост від" not in message
    assert len(message) <= 4096
