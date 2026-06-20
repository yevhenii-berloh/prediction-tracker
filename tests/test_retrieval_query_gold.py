import json

from retrieval.build_query_gold import (
    build_query_prompt,
    ensure_manual_stub,
    generate_queries,
    run_gold,
)


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def complete(self, prompt: str, system: str | None = None) -> str:
        self.calls.append(prompt)
        return "  згенерований запит  "


def test_prompt_uses_claim_text_for_content():
    row = {"id": "a", "claim_text": "Авдіївка впаде", "situation": "взимку 2024"}
    p = build_query_prompt(row, "claim_text")
    assert "Авдіївка впаде" in p and "не копіюй" in p.lower()


def test_prompt_uses_situation_for_context():
    row = {"id": "a", "claim_text": "Авдіївка впаде", "situation": "взимку 2024"}
    p = build_query_prompt(row, "situation")
    assert "взимку 2024" in p


async def test_generate_two_queries_when_situation_present():
    row = {"id": "a", "claim_text": "c", "situation": "s"}
    recs = await generate_queries(row, FakeLLM())
    assert [r["source_field"] for r in recs] == ["claim_text", "situation"]
    assert all(r["target_id"] == "a" for r in recs)
    assert recs[0]["query"] == "згенерований запит"  # обрізані пробіли


async def test_generate_skips_context_when_no_situation():
    row = {"id": "a", "claim_text": "c", "situation": ""}
    recs = await generate_queries(row, FakeLLM())
    assert [r["source_field"] for r in recs] == ["claim_text"]


async def test_run_gold_writes_records(tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "id": "a",
                    "claim_text": "c",
                    "situation": "s",
                    "topic": "війна",
                    "prediction_date": "2024-01-01",
                }
            ]
        )
    )
    out = tmp_path / "gold.json"
    await run_gold(corpus, out, n=1, seed=1, llm=FakeLLM())
    recs = json.loads(out.read_text())
    assert {r["source_field"] for r in recs} == {"claim_text", "situation"}


def test_ensure_manual_stub_creates_empty_list(tmp_path):
    path = tmp_path / "manual.json"
    ensure_manual_stub(path)
    assert json.loads(path.read_text()) == []
    path.write_text('[{"query": "x", "target_id": "a"}]')
    ensure_manual_stub(path)  # не перетирає наявний
    assert len(json.loads(path.read_text())) == 1
