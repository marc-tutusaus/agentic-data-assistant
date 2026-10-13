"""Build an LLM client from configuration (LLM_PROVIDER / LLM_MODEL / API keys)."""

from __future__ import annotations

import os

from .anthropic_client import AnthropicClient
from .base import LLMClient
from .openai_compat import OpenAICompatClient
from .openai_responses import OpenAIResponsesClient

# OpenAI-compatible Chat Completions servers: provider -> (base_url, api key env var, default model)
OPENAI_COMPATIBLE = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-2.5-flash"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "mistral-large-latest"),
    "ollama": ("http://localhost:11434/v1", None, "qwen2.5:14b"),
    "lmstudio": ("http://localhost:1234/v1", None, "qwen2.5-14b-instruct"),
}
PROVIDERS = ["anthropic", "openai", *OPENAI_COMPATIBLE]

# USD per 1M tokens (input, output) - used for cost reporting only
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o-mini": (0.15, 0.60),
}


def create_llm(provider: str | None = None, model: str | None = None) -> LLMClient:
    provider = (provider or os.getenv("LLM_PROVIDER", "anthropic")).lower()
    model = model or os.getenv("LLM_MODEL") or None

    if provider == "anthropic":
        return AnthropicClient(
            model=model or "claude-opus-5-5",
            api_key=os.getenv("ANTHROPIC_API_KEY") or None,
            effort=os.getenv("LLM_EFFORT", "medium") or None,
            fallbacks=os.getenv("ANTHROPIC_FALLBACKS", "default") != "off",
        )
    if provider == "openai":
        return OpenAIResponsesClient(
            model=model or "gpt-5.6-luna",
            api_key=os.getenv("OPENAI_API_KEY") or None,
            effort=os.getenv("LLM_EFFORT", "medium") or None,
        )
    if provider in OPENAI_COMPATIBLE:
        base_url, key_env, default_model = OPENAI_COMPATIBLE[provider]
        base_url = os.getenv("LLM_BASE_URL") or base_url
        return OpenAICompatClient(
            provider, model or default_model, api_key=os.getenv(key_env) if key_env else None, base_url=base_url
        )
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Choose one of: {', '.join(PROVIDERS)}")


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return round((input_tokens * price[0] + output_tokens * price[1]) / 1_000_000, 6)
