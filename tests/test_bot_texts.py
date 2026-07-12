from prophet_checker.bot.texts import (
    START_TEXT,
    TELEGRAM_MESSAGE_LIMIT,
    truncate_for_telegram,
)

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
