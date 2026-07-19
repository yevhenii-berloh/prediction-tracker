"""Статичні тексти бота, хелпер під ліміт повідомлення Telegram і блок джерел."""

from __future__ import annotations

import re

from prophet_checker.models.domain import Citation

TELEGRAM_MESSAGE_LIMIT = 4096
SOURCES_HEADER = "Джерела:"
_MARKER_RE = re.compile(r"\[(\d+)\]")

START_TEXT = (
    "Привіт! Я бот проєкту prediction-tracker.\n"
    "\n"
    "Я відповідаю на питання про прогнози українських публічних осіб "
    "і кажу, чи вони справдилися. Зараз у базі — прогнози Олексія "
    "Арестовича з його Telegram-каналу.\n"
    "\n"
    "Спробуй спитати:\n"
    "• Що Арестович прогнозував про завершення війни?\n"
    "• Які прогнози про Крим справдилися?\n"
    "• Що він казав про F-16?\n"
    "\n"
    "Аналіз автоматизований і може містити неточності."
)

ERROR_TEXT = "⚠️ Щось пішло не так. Спробуй ще раз трохи пізніше."

NOT_TEXT_TEXT = "Я розумію лише текстові питання — напиши, будь ласка, словами."

UNKNOWN_COMMAND_TEXT = "Не знаю такої команди. Просто напиши питання текстом."


def truncate_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    # "…" займає один символ — ріжемо на один коротше, щоб текст разом з ним уклався в limit
    return text[: limit - 1] + "…"


def _citation_line(citation: Citation) -> str:
    markers = ""
    for marker in citation.markers:
        markers += f"[{marker}]"
    day = citation.published_at.strftime("%d.%m.%Y")
    return f'<a href="{citation.url}">{markers} Пост від {day}</a>'


def _render_block(citations: list[Citation]) -> str:
    lines = [SOURCES_HEADER]
    for citation in citations:
        lines.append(_citation_line(citation))
    return "\n".join(lines)


def compose_answer_message(text: str, citations: list[Citation]) -> str:
    """Скласти повідомлення: спершу обрізати тіло, потім лишити тільки ті цитати,
    чиї маркери пережили обрізання. Інакше в блоці лишиться рядок без посилання в тексті."""
    if not citations:
        return truncate_for_telegram(text)

    budget = TELEGRAM_MESSAGE_LIMIT - len(_render_block(citations)) - 2
    body = truncate_for_telegram(text, limit=max(budget, 1))

    survived = set()
    for match in _MARKER_RE.finditer(body):
        survived.add(int(match.group(1)))

    kept = []
    for citation in citations:
        if survived.intersection(citation.markers):
            kept.append(citation)
    if not kept:
        return body
    return f"{body}\n\n{_render_block(kept)}"
