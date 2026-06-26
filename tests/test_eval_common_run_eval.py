from pydantic import BaseModel

from eval_common import run_eval
from eval_common.models import EvalCase, EvalMetadata, ScoreCard


class _In(BaseModel):
    n: int


class _Out(BaseModel):
    doubled: int


class _M(BaseModel):
    total: int


class _SumScorer:
    name = "sum"

    async def score(self, run):
        return ScoreCard(scorer=self.name, score=float(run.result.doubled))


async def test_run_eval_end_to_end(tmp_path):
    cases = [EvalCase(id=str(i), input=_In(n=i)) for i in range(3)]

    async def run_one(case):
        return _Out(doubled=case.input.n * 2)

    def aggregate(scored):
        return _M(total=int(sum(c.score for s in scored for c in s.cards)))

    meta = EvalMetadata(eval_name="t", created_at="2026-01-01T00:00:00Z", n_cases=3)
    report = await run_eval(cases, run_one, [_SumScorer()], aggregate, meta, tmp_path)

    assert report.metrics.total == 0 + 2 + 4
    assert len(report.runs) == 3
    assert (tmp_path / "report.json").exists()
    assert (tmp_path / "report.md").exists()


async def test_run_eval_handles_sut_failure_with_guarded_scorers(tmp_path):
    class _GuardSum:
        name = "sum"

        async def score(self, run):
            score = float(run.result.doubled) if run.result is not None else None
            return ScoreCard(scorer=self.name, score=score)

    class _GuardFlag:
        name = "ok"

        async def score(self, run):
            return ScoreCard(scorer=self.name, score=0.0 if run.result is None else 1.0)

    cases = [EvalCase(id=str(i), input=_In(n=i)) for i in range(3)]

    async def run_one(case):
        if case.id == "1":
            raise RuntimeError("boom")
        return _Out(doubled=case.input.n * 2)

    def aggregate(scored):
        vals = [c.score for s in scored for c in s.cards if c.score is not None]
        return _M(total=int(sum(vals)))

    meta = EvalMetadata(eval_name="t", created_at="2026-01-01T00:00:00Z", n_cases=3)
    report = await run_eval(cases, run_one, [_GuardSum(), _GuardFlag()], aggregate, meta, tmp_path)

    by_id = {s.run.case.id: s for s in report.runs}
    # failed case: result None, both scorers handled it (sum→None, ok→0.0)
    assert by_id["1"].run.result is None
    sum_card = next(c for c in by_id["1"].cards if c.scorer == "sum")
    ok_card = next(c for c in by_id["1"].cards if c.scorer == "ok")
    assert sum_card.score is None
    assert ok_card.score == 0.0
    # each run has 2 cards (2 scorers), in order
    assert [c.scorer for c in by_id["0"].cards] == ["sum", "ok"]
    # aggregate skipped the None: doubled 0 + doubled 4 = 4, plus ok-flags (1+0+1)=2 → 6
    assert report.metrics.total == 6
