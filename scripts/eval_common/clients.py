from __future__ import annotations

import os

from prophet_checker.llm.client import LLMClient

PROVIDER_API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
}


def parse_model_id(model_id: str) -> tuple[str, str]:
    provider, _, model = model_id.partition("/")
    if not provider or not model:
        raise ValueError(f"model_id must be 'provider/model', got {model_id!r}")
    return provider, model


def build_eval_llm(model_id: str, *, temperature: float | None = 0.0) -> LLMClient:
    """Build an LLMClient from 'provider/model' using env-var API keys (temp=0 for evals)."""
    provider, model = parse_model_id(model_id)
    if provider not in PROVIDER_API_KEY_ENV:
        raise ValueError(f"Unknown provider {provider!r}. Supported: {list(PROVIDER_API_KEY_ENV)}")
    env_var = PROVIDER_API_KEY_ENV[provider]
    api_key = os.environ.get(env_var)
    if not api_key:
        raise RuntimeError(f"Missing API key for {provider!r}: set env var {env_var}")
    return LLMClient(provider=provider, model=model, api_key=api_key, temperature=temperature)
