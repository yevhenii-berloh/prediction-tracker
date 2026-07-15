from unittest.mock import AsyncMock, patch

import litellm
import pytest

from prophet_checker.llm.embedding import (
    MAX_EMBED_TOKENS,
    EmbeddingClient,
    truncate_to_token_budget,
)

EMBED_MODEL = "text-embedding-3-small"


def _token_count(text: str, model: str = EMBED_MODEL) -> int:
    return len(litellm.encode(model=model, text=text))


@pytest.mark.asyncio
async def test_embedding_client_default_model():
    client = EmbeddingClient(api_key="test-key")
    mock_response = AsyncMock()
    mock_response.data = [{"embedding": [0.1, 0.2, 0.3]}]

    with patch("prophet_checker.llm.embedding.aembedding", return_value=mock_response) as mock_call:
        result = await client.embed("Test text")

    assert result == [0.1, 0.2, 0.3]
    call_kwargs = mock_call.call_args.kwargs
    assert call_kwargs["model"] == "text-embedding-3-small"
    assert call_kwargs["api_key"] == "test-key"
    assert call_kwargs["input"] == ["Test text"]


@pytest.mark.asyncio
async def test_embedding_client_custom_model():
    client = EmbeddingClient(model="cohere/embed-english-v3.0", api_key="cohere-key")
    mock_response = AsyncMock()
    mock_response.data = [AsyncMock(embedding=[0.5] * 1024)]

    with patch("prophet_checker.llm.embedding.aembedding", return_value=mock_response) as mock_call:
        await client.embed("Test")

    assert mock_call.call_args.kwargs["model"] == "cohere/embed-english-v3.0"


@pytest.mark.asyncio
async def test_embedding_client_no_api_key_passes_none():
    client = EmbeddingClient()
    mock_response = AsyncMock()
    mock_response.data = [AsyncMock(embedding=[0.0])]

    with patch("prophet_checker.llm.embedding.aembedding", return_value=mock_response) as mock_call:
        await client.embed("Test")

    assert mock_call.call_args.kwargs["api_key"] is None


def test_truncate_keeps_text_within_budget_unchanged():
    text = "Короткий прогноз про курс гривні до кінця року."

    assert truncate_to_token_budget(text, EMBED_MODEL, MAX_EMBED_TOKENS) == text


def test_truncate_shrinks_oversized_text_to_budget():
    text = "прогноз " * 5000  # Cyrillic — щільна токенізація, гарантовано понад ліміт
    assert _token_count(text) > MAX_EMBED_TOKENS  # guard: вхід справді завеликий

    truncated = truncate_to_token_budget(text, EMBED_MODEL, MAX_EMBED_TOKENS)

    assert _token_count(truncated) <= MAX_EMBED_TOKENS


@pytest.mark.asyncio
async def test_embed_truncates_oversized_input_before_request():
    client = EmbeddingClient(api_key="test-key")
    long_text = "прогноз " * 5000
    mock_response = AsyncMock()
    mock_response.data = [{"embedding": [0.1, 0.2, 0.3]}]

    with patch("prophet_checker.llm.embedding.aembedding", return_value=mock_response) as mock_call:
        await client.embed(long_text)

    sent = mock_call.call_args.kwargs["input"][0]
    assert _token_count(sent) <= MAX_EMBED_TOKENS
