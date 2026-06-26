import json

from pydantic import BaseModel

from eval_common.models import (
    EvalCase,
    EvalMetadata,
    EvalReport,
    EvalRun,
    ScoreCard,
    ScoredRun,
)
from eval_common.report import write_report


class _In(BaseModel):
    question: str


class _M(BaseModel):
    mean: float


def _report() -> EvalReport:
    case = EvalCase(id="1", input=_In(question="що?"))
    run = EvalRun(case=case, result=None, latency_s=0.1, error=None)
    scored = ScoredRun(run=run, cards=[ScoreCard(scorer="x", score=1.0)])
    return EvalReport(
        metadata=EvalMetadata(eval_name="t", created_at="2026-01-01T00:00:00Z", n_cases=1),
        metrics=_M(mean=0.5),
        runs=[scored],
    )


def test_write_report_persists_subclass_fields(tmp_path):
    write_report(_report(), tmp_path)
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    # SerializeAsAny: поле сабкласу вижило
    assert data["runs"][0]["run"]["case"]["input"]["question"] == "що?"
    assert data["metrics"]["mean"] == 0.5


def test_write_report_md_has_header(tmp_path):
    write_report(_report(), tmp_path)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "t" in md  # eval_name
    assert "cases: 1" in md
