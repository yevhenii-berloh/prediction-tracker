from retrieval.embed_corpus import build_representation_text


def test_claim_text():
    row = {"claim_text": "C", "situation": "S"}
    assert build_representation_text(row, "claim_text") == "C"


def test_situation_skips_when_empty():
    assert build_representation_text({"claim_text": "C", "situation": ""}, "situation") is None


def test_claim_situation_concat():
    row = {"claim_text": "C", "situation": "S"}
    assert build_representation_text(row, "claim_situation") == "C\nS"


def test_claim_situation_falls_back_to_claim_when_no_situation():
    assert build_representation_text({"claim_text": "C", "situation": ""}, "claim_situation") == "C"
