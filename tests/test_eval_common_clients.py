import pytest

from eval_common.clients import build_eval_llm, parse_model_id
from prophet_checker.llm.client import LLMClient


def test_parse_model_id_valid():
    assert parse_model_id("gemini/gemini-3.1-flash-lite-preview") == (
        "gemini",
        "gemini-3.1-flash-lite-preview",
    )


def test_parse_model_id_rejects_bare():
    with pytest.raises(ValueError):
        parse_model_id("gpt-5-mini")


def test_parse_model_id_rejects_trailing_slash():
    with pytest.raises(ValueError):
        parse_model_id("anthropic/")


def test_parse_model_id_rejects_leading_slash():
    with pytest.raises(ValueError):
        parse_model_id("/model")


def test_build_eval_llm_unknown_provider():
    with pytest.raises(ValueError):
        build_eval_llm("foo/bar")


def test_build_eval_llm_missing_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        build_eval_llm("gemini/gemini-3.1-flash-lite-preview")


def test_build_eval_llm_happy(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    llm = build_eval_llm("gemini/gemini-3.1-flash-lite-preview")
    assert isinstance(llm, LLMClient)
