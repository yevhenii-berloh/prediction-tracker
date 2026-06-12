from __future__ import annotations

import logging

from litellm import acompletion

logger = logging.getLogger(__name__)

# Opus 4.7+/Fable: семплінг-параметри (temperature/top_p/top_k) видалені з API —
# запит, що містить temperature, повертає 400 invalid_request_error.
_NO_TEMPERATURE_MODEL_PREFIXES = ("claude-opus-4-7", "claude-opus-4-8", "claude-fable")


class LLMClient:
    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        temperature: float | None = 0.1,
        num_retries: int = 3,
    ):
        self._model = f"{provider}/{model}" if provider != "openai" else model
        self._api_key = api_key
        self._temperature = temperature
        self._num_retries = num_retries
        if temperature is not None and model.startswith(_NO_TEMPERATURE_MODEL_PREFIXES):
            logger.warning(
                "Model %s does not accept temperature (removed from API) — "
                "dropping temperature=%s from requests",
                model,
                temperature,
            )
            self._temperature = None

    async def complete(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {}
        if self._temperature is not None:
            kwargs["temperature"] = self._temperature

        response = await acompletion(
            model=self._model,
            messages=messages,
            api_key=self._api_key,
            num_retries=self._num_retries,
            **kwargs,
        )
        return response.choices[0].message.content
