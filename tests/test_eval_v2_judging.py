from extraction.eval_v2.eval_models import PooledClaim
from extraction.eval_v2.judging import judge_post, reference_size


class ScriptedJudge:
    """Різні відповіді по черзі — FakeJudge каркаса вміє лише одну."""

    id = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.systems: list[str] = []

    async def assess(self, prompt: str, *, system: str) -> str:
        self.systems.append(system)
        return self._responses.pop(0)


_VALID = (
    '{"claim_grounded": true, "situation_grounded": true, '
    '"passes_rubric": true, "truncated": false}'
)
_NOT_RUBRIC = (
    '{"claim_grounded": true, "situation_grounded": true, '
    '"passes_rubric": false, "truncated": false}'
)
_UNGROUNDED = (
    '{"claim_grounded": false, "situation_grounded": true, '
    '"passes_rubric": true, "truncated": false}'
)


def _claim(index: int) -> PooledClaim:
    return PooledClaim(
        claim_id=f"p1:{index}", claim_text=f"claim {index}", situation="s", models=["a"]
    )


async def test_reference_set_is_valid_claims_plus_missed():
    judge = ScriptedJudge([_VALID, _NOT_RUBRIC, '{"missed": [{"text": "ще одне"}]}'])

    judgement = await judge_post("p1", "текст", [_claim(0), _claim(1)], judge)

    assert reference_size(judgement) == 2  # один валідний + один missed


async def test_ungrounded_claim_stays_out_of_the_reference_set():
    judge = ScriptedJudge([_UNGROUNDED, '{"missed": []}'])

    judgement = await judge_post("p1", "текст", [_claim(0)], judge)

    assert judgement.verdicts[0].is_hallucinated is True
    assert reference_size(judgement) == 0


async def test_empty_claim_list_still_asks_about_missed():
    judge = ScriptedJudge(['{"missed": [{"text": "усі проґавили"}]}'])

    judgement = await judge_post("p1", "текст", [], judge)

    assert judgement.verdicts == []
    assert reference_size(judgement) == 1  # мовчання всіх ≠ порожній reference set


async def test_unparsable_check_becomes_sentinel_and_is_counted():
    judge = ScriptedJudge(["суддя щось намолов", '{"missed": []}'])

    judgement = await judge_post("p1", "текст", [_claim(0)], judge)

    assert judgement.judge_errors == 1
    assert judgement.verdicts[0].is_valid is False
    assert judgement.verdicts[0].reason == "judge-unparsable"
    assert reference_size(judgement) == 0


async def test_unparsable_missed_call_does_not_kill_the_post():
    judge = ScriptedJudge([_VALID, "знову не json"])

    judgement = await judge_post("p1", "текст", [_claim(0)], judge)

    assert judgement.missed == []
    assert judgement.judge_errors == 1
    assert reference_size(judgement) == 1  # валідний claim вцілів


async def test_each_call_type_uses_its_own_system_prompt():
    judge = ScriptedJudge([_VALID, '{"missed": []}'])

    await judge_post("p1", "текст", [_claim(0)], judge)

    assert len(set(judge.systems)) == 2  # перевірний і missed — різні системні промпти


async def test_verdicts_keep_claim_ids():
    judge = ScriptedJudge([_VALID, _VALID, '{"missed": []}'])

    judgement = await judge_post("p1", "текст", [_claim(0), _claim(1)], judge)

    assert [v.claim_id for v in judgement.verdicts] == ["p1:0", "p1:1"]
