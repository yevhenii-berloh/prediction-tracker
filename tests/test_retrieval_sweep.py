from retrieval.embed_corpus import run_sweep


class FakeEmbedder:
    def __init__(self, model):
        self.model = model

    async def embed(self, text: str) -> list[float]:
        return [float(len(text)), float(self.model == "m2")]


class FakeStore:
    def __init__(self):
        self.rows = []
        self.recreated = []
        self.ensured = False

    async def ensure_table(self):
        self.ensured = True

    async def recreate(self, config):
        self.recreated.append(config)

    async def add(self, config, prediction_id, embedding):
        self.rows.append((config, prediction_id, embedding))

    async def search(self, config, query, limit):
        return []


async def test_sweep_populates_per_config_and_skips_empty_situation():
    corpus = [
        {"id": "a", "claim_text": "AA", "situation": "sit"},
        {"id": "b", "claim_text": "BB", "situation": ""},
    ]
    store = FakeStore()
    await run_sweep(
        corpus,
        configs=[("m1", "claim_text"), ("m1", "situation")],
        embedder_factory=FakeEmbedder,
        store=store,
    )
    assert store.ensured is True
    # claim_text: обидва прогнози; situation: лише "a" (b має порожню situation)
    ct = [r for r in store.rows if r[0] == "m1__claim_text"]
    sit = [r for r in store.rows if r[0] == "m1__situation"]
    assert {r[1] for r in ct} == {"a", "b"}
    assert {r[1] for r in sit} == {"a"}
    assert "m1__claim_text" in store.recreated and "m1__situation" in store.recreated
