# scripts/eval_common/models.py
from __future__ import annotations

from pydantic import BaseModel, SerializeAsAny


class EvalCase(BaseModel):
    id: str
    # SerializeAsAny: зберегти поля сабкласу при дампі в JSON (узагальнена база, типізований сабтайп)
    input: SerializeAsAny[BaseModel]
    labels: SerializeAsAny[BaseModel] | None = None


class EvalRun(BaseModel):
    case: EvalCase
    result: SerializeAsAny[BaseModel] | None = None  # вихід SUT; None якщо SUT впав
    latency_s: float
    error: str | None = None  # тип винятку, не повідомлення/payload


class ScoreCard(BaseModel):
    scorer: str
    score: float | None  # None = не застосовано (SUT впав / нерелевантно)
    detail: SerializeAsAny[BaseModel] | None = None


class ScoredRun(BaseModel):
    run: EvalRun
    cards: list[ScoreCard]


class EvalMetadata(BaseModel):
    eval_name: str
    created_at: str  # UTC ISO, ставить консумер
    n_cases: int
    sut_models: dict[str, str] = {}
    judge_id: str | None = None
    prompt_fingerprints: dict[str, str] = {}
    dataset_path: str | None = None  # None якщо інлайн/синтез


class EvalReport(BaseModel):
    metadata: EvalMetadata
    metrics: SerializeAsAny[BaseModel]  # Metrics-сабтайп консумера
    runs: list[ScoredRun]
