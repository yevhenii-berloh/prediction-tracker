from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from prophet_checker.llm.client import LLMClient


async def test_llm_client_complete():
    client = LLMClient(provider="openai", model="gpt-4o-mini", api_key="sk-test")
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content="Test response"))]

    with patch("prophet_checker.llm.client.acompletion", return_value=mock_response) as mock_call:
        result = await client.complete("Test prompt")
        assert result == "Test response"
        mock_call.assert_called_once()


async def test_llm_client_complete_with_system():
    client = LLMClient(provider="openai", model="gpt-4o-mini", api_key="sk-test")
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content="Answer"))]

    with patch("prophet_checker.llm.client.acompletion", return_value=mock_response) as mock_call:
        result = await client.complete("Question", system="You are an analyst")
        assert result == "Answer"
        call_args = mock_call.call_args
        messages = call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are an analyst"


async def test_temperature_dropped_for_opus_4_8():
    """Opus 4.7+/Fable не приймають temperature (400) — клієнт мусить його не слати."""
    client = LLMClient(
        provider="anthropic", model="claude-opus-4-8", api_key="sk-test", temperature=0.0
    )
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content="ok"))]

    with patch("prophet_checker.llm.client.acompletion", return_value=mock_response) as mock_call:
        await client.complete("Test")
        assert "temperature" not in mock_call.call_args.kwargs


async def test_temperature_dropped_for_opus_5():
    """Opus 5 теж не приймає temperature — інакше кожен виклик судді eval-v2 дає 400."""
    client = LLMClient(
        provider="anthropic", model="claude-opus-5", api_key="sk-test", temperature=0.0
    )
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content="ok"))]

    with patch("prophet_checker.llm.client.acompletion", return_value=mock_response) as mock_call:
        await client.complete("Test")
        assert "temperature" not in mock_call.call_args.kwargs


async def test_temperature_kept_for_opus_4_6():
    client = LLMClient(
        provider="anthropic", model="claude-opus-4-6", api_key="sk-test", temperature=0.0
    )
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content="ok"))]

    with patch("prophet_checker.llm.client.acompletion", return_value=mock_response) as mock_call:
        await client.complete("Test")
        assert mock_call.call_args.kwargs["temperature"] == 0.0


async def test_complete_accumulates_usage():
    """cost_per_post рахується з цих лічильників — інакше токени провайдера губляться."""
    client = LLMClient(provider="gemini", model="gemini-3.1-flash-lite", api_key="k")
    mock_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )

    with patch("prophet_checker.llm.client.acompletion", return_value=mock_response):
        await client.complete("Test")
        await client.complete("Test")

    assert client.prompt_tokens == 22
    assert client.completion_tokens == 14

    client.reset_usage()
    assert client.prompt_tokens == 0
    assert client.completion_tokens == 0


async def test_missing_usage_does_not_break_completion():
    """Провайдер без usage не має валити прогін — метрика просто недорахує цей виклик."""
    client = LLMClient(provider="gemini", model="gemini-3.1-flash-lite", api_key="k")
    mock_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=None,
    )

    with patch("prophet_checker.llm.client.acompletion", return_value=mock_response):
        assert await client.complete("Test") == "ok"

    assert client.prompt_tokens == 0


async def test_temperature_none_omits_param():
    client = LLMClient(
        provider="openai", model="gpt-4o-mini", api_key="sk-test", temperature=None
    )
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content="ok"))]

    with patch("prophet_checker.llm.client.acompletion", return_value=mock_response) as mock_call:
        await client.complete("Test")
        assert "temperature" not in mock_call.call_args.kwargs
