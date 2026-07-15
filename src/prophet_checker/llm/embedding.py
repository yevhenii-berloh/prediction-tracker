from __future__ import annotations

from litellm import aembedding, decode, encode

# text-embedding-3-* приймає щонайбільше 8192 токени; тримаємо запас на дрейф
# ре-енкодингу межі та можливий різнобій клієнтського/серверного токенайзера.
MAX_EMBED_TOKENS = 8191


def truncate_to_token_budget(text: str, model: str, max_tokens: int) -> str:
    """Обрізає text до max_tokens токенів моделі. Кирилиця в cl100k_base щільна
    (~5× символьної), тож рахуємо саме токени, а не символи."""
    tokens = encode(model=model, text=text)
    if len(tokens) <= max_tokens:
        return text
    return decode(model=model, tokens=tokens[:max_tokens])


class EmbeddingClient:
    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        num_retries: int = 3,
    ):
        self._model = model
        self._api_key = api_key
        self._num_retries = num_retries

    async def embed(self, text: str) -> list[float]:
        text = truncate_to_token_budget(text, self._model, MAX_EMBED_TOKENS)
        response = await aembedding(
            model=self._model,
            input=[text],
            api_key=self._api_key,
            num_retries=self._num_retries,
        )
        return response.data[0].get("embedding")
