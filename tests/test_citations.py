from datetime import date

from prophet_checker.models.domain import Prediction, RetrievedPrediction
from prophet_checker.query.citations import resolve

ID_A = "7c9f4e21-3a8b-4d15-9e02-6b1f8a4c7d33"
ID_B = "b4e18d70-52ac-4f39-8c61-9d3e7a0f2b5e"
UNKNOWN = "00000000-0000-4000-8000-000000000000"


def _prediction(pid: str, doc: str) -> Prediction:
    return Prediction(
        id=pid,
        document_id=doc,
        person_id="p1",
        claim_text="твердження",
        prediction_date=date(2020, 8, 12),
    )


def _sources(*pairs: tuple[str, str]) -> list[RetrievedPrediction]:
    out = []
    for rank, (pid, doc) in enumerate(pairs, start=1):
        out.append(RetrievedPrediction(prediction=_prediction(pid, doc), distance=0.1, rank=rank))
    return out


def test_markers_numbered_by_first_appearance():
    sources = _sources((ID_A, "d1"), (ID_B, "d2"))
    # у тексті B згадано ПЕРШИМ, хоча його rank другий
    answer = f"друге [{ID_B}] і перше [{ID_A}]"

    result = resolve(answer, sources)

    assert result.text == "друге [1] і перше [2]"
    assert [(r.marker, r.prediction_id) for r in result.refs] == [(1, ID_B), (2, ID_A)]


def test_repeated_id_keeps_one_number_but_two_refs():
    sources = _sources((ID_A, "d1"))
    answer = f"раз [{ID_A}] і ще раз [{ID_A}]"

    result = resolve(answer, sources)

    assert result.text == "раз [1] і ще раз [1]"
    assert len(result.refs) == 2
    assert {r.marker for r in result.refs} == {1}


def test_unknown_id_is_cut_out():
    sources = _sources((ID_A, "d1"))
    answer = f"відоме [{ID_A}] і вигадане [{UNKNOWN}]"

    result = resolve(answer, sources)

    assert UNKNOWN not in result.text
    assert result.text == "відоме [1] і вигадане "
    assert len(result.refs) == 1


def test_bare_identifier_is_cut_out():
    sources = _sources((ID_A, "d1"))
    answer = f"витік {ID_A} у прозі"

    result = resolve(answer, sources)

    assert ID_A not in result.text
    assert result.refs == []


def test_text_unmarked_has_no_markers():
    sources = _sources((ID_A, "d1"))
    answer = f"твердження [{ID_A}] далі"

    result = resolve(answer, sources)

    assert result.text_unmarked == "твердження  далі"
    assert "[1]" not in result.text_unmarked


def test_offset_points_at_marker_in_text():
    sources = _sources((ID_A, "d1"))
    answer = f"твердження [{ID_A}] далі"

    result = resolve(answer, sources)

    offset = result.refs[0].offset
    assert result.text[offset : offset + 3] == "[1]"


def test_ref_carries_document_id():
    sources = _sources((ID_A, "doc-42"))

    result = resolve(f"текст [{ID_A}]", sources)

    assert result.refs[0].document_id == "doc-42"
