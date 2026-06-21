from __future__ import annotations

from prophet_checker.models.domain import Prediction


def embedding_text(pred: Prediction) -> str:
    """Текст, що йде в ембединг прогнозу: claim + situation (fallback на claim, якщо situation
    порожня). Має збігатися з eval-репрезентацією 'claim_situation'
    (`scripts/retrieval/embed_corpus.py`), на якій обрано цю конфігурацію."""
    situation = (pred.situation or "").strip()
    return f"{pred.claim_text}\n{situation}" if situation else pred.claim_text
