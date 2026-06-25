from datetime import date

from fakes import FakePersonRepo, FakePredictionRepo, FakeVectorStore
from ingestion.backfill_embeddings import backfill

from prophet_checker.models.domain import Person, Prediction


class FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [float(len(text))]


def _pred(pid, person_id, claim, situation):
    return Prediction(
        id=pid,
        document_id="d",
        person_id=person_id,
        claim_text=claim,
        situation=situation,
        prediction_date=date(2024, 1, 1),
    )


async def test_backfill_embeds_claim_plus_situation_for_all():
    person_repo = FakePersonRepo()
    await person_repo.save(Person(id="p1", name="A"))
    pred_repo = FakePredictionRepo()
    await pred_repo.save(_pred("x", "p1", "C", "S"))
    await pred_repo.save(_pred("y", "p1", "D", None))
    store = FakeVectorStore()

    n = await backfill(person_repo, pred_repo, store, FakeEmbedder())

    assert n == 2
    stored = dict(store._entries)
    assert stored["x"] == [float(len("C\nS"))]  # claim+situation
    assert stored["y"] == [float(len("D"))]  # fallback на claim


async def test_backfill_skips_already_embedded():
    person_repo = FakePersonRepo()
    await person_repo.save(Person(id="p1", name="A"))
    pred_repo = FakePredictionRepo()
    await pred_repo.save(_pred("x", "p1", "C", "S"))
    await pred_repo.save(_pred("y", "p1", "D", "T"))
    store = FakeVectorStore()
    await store.store_embedding("x", [9.9])  # x уже має ембединг → має бути пропущений

    n = await backfill(person_repo, pred_repo, store, FakeEmbedder())

    assert n == 1  # заембеджено лише y
    assert ("x", [9.9]) in store._entries  # старий ембединг x не перезаписаний
    assert ("y", [float(len("D\nT"))]) in store._entries
