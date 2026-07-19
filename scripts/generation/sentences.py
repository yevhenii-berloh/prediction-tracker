"""Вирізання речення за позицією маркера — потрібне лише citation-судді.

Живе в scripts/, а не в src/: продакшн речень не потребує (боту вони ні до чого),
а український поділ крихкий через дати. Помилка спліту тут зіпсує вимірювання,
а не повідомлення живому користувачу.
"""

from __future__ import annotations

import re

# Межа речення — крапка/!/? + пробіл + велика літера. Дата (12.08.2020) і скорочення
# ("2020 р. він") цьому не відповідають: після крапки в даті йде цифра, а після "р." —
# мала літера. Цього досить для наших відповідей і не тягне NLP-залежність.
_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЇІЄҐA-Z])")


def sentence_at(text: str, offset: int) -> str:
    """Речення, всередині якого стоїть символ на позиції offset."""
    start = 0
    end = len(text)
    for match in _BOUNDARY_RE.finditer(text):
        if match.end() <= offset:
            start = match.end()
            continue
        if match.start() > offset:
            end = match.start()
            break
    return text[start:end].strip()
