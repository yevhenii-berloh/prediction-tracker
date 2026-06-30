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


_ROW = {
    "id": "a",
    "claim_text": "Авдіївка впаде",
    "situation": "взимку 2024",
    "prediction_date": "2023-11-01",
    "topic": "війна",
}


def test_prompt_includes_full_context_for_grounding():
    # повний контекст у промпті обох сімей → є звідки взяти якір
    for field in ("claim_text", "situation"):
        p = build_query_prompt(_ROW, field)
        assert "Авдіївка впаде" in p  # зміст
        assert "взимку 2024" in p  # обставини
        assert "2023-11-01" in p  # дата для абсолютного періоду


def test_prompt_emphasis_differs_by_source_field():
    content = build_query_prompt(_ROW, "claim_text")
    context = build_query_prompt(_ROW, "situation")
    assert "що саме має статися" in content  # акцент на зміст
    assert "період і подію" in context  # акцент на обставини


def test_prompt_demands_anchors_and_paraphrase():
    p = build_query_prompt(_ROW, "claim_text")
    assert "перефразуй" in p.lower()  # не копіювати формулювання
    assert "форкастинг" in p  # форкастинг присутній лише як заборона (негативний приклад)


def test_query_prompt_is_prediction_centric():
    prompt = build_query_prompt(
        {
            "claim_text": "війна закінчиться у 2025",
            "situation": "на тлі переговорів",
            "prediction_date": "2024-01-01",
            "topic": "війна",
        },
        "claim_text",
    )
    # ретроспективна рамка перевірки прогнозу присутня
    assert "РЕТРОСПЕКТИВ" in prompt
    assert "прогнозував" in prompt
    assert "НЕ проси спрогнозувати майбутнє" in prompt
    # форкастинг згадується лише як НЕГАТИВНИЙ приклад
    assert "(форкастинг)" in prompt


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
