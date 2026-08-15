from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChannelReport(BaseModel):
    person_source_id: str
    posts_seen: int = 0
    posts_with_predictions: int = 0
    posts_failed: int = 0  # екстракція впала — курсор не рухаємо, пост повернеться
    predictions_extracted: int = 0
    cursor_advanced_to: datetime | None = None
    error: str | None = None


class CycleReport(BaseModel):
    started_at: datetime
    finished_at: datetime
    channels_processed: list[ChannelReport] = Field(default_factory=list)
