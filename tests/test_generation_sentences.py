from generation.sentences import sentence_at


def test_returns_sentence_containing_offset():
    text = "Перше речення. Друге речення [1] тут. Третє."
    offset = text.index("[1]")

    assert sentence_at(text, offset) == "Друге речення [1] тут."


def test_date_does_not_split_sentence():
    text = "Прогноз від 12.08.2020 справдився [1]."
    offset = text.index("[1]")

    assert sentence_at(text, offset) == text


def test_abbreviation_does_not_split_sentence():
    text = "У 2020 р. він казав про це [1]."
    offset = text.index("[1]")

    assert sentence_at(text, offset) == text


def test_first_sentence_when_offset_at_start():
    text = "Перше [1] речення. Друге."
    offset = text.index("[1]")

    assert sentence_at(text, offset) == "Перше [1] речення."


def test_last_sentence_has_no_trailing_boundary():
    text = "Перше. Останнє [2] речення."
    offset = text.index("[2]")

    assert sentence_at(text, offset) == "Останнє [2] речення."
