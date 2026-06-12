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


async def test_temperature_kept_for_opus_4_6():
    client = LLMClient(
        provider="anthropic", model="claude-opus-4-6", api_key="sk-test", temperature=0.0
    )
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content="ok"))]

    with patch("prophet_checker.llm.client.acompletion", return_value=mock_response) as mock_call:
        await client.complete("Test")
        assert mock_call.call_args.kwargs["temperature"] == 0.0


async def test_temperature_none_omits_param():
    client = LLMClient(
        provider="openai", model="gpt-4o-mini", api_key="sk-test", temperature=None
    )
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content="ok"))]

    with patch("prophet_checker.llm.client.acompletion", return_value=mock_response) as mock_call:
        await client.complete("Test")
        assert "temperature" not in mock_call.call_args.kwargs
