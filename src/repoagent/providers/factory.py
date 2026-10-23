"""Provider factory and selection based on REPOAGENT_PROVIDER."""

from __future__ import annotations

import os

from repoagent.providers.base import LLMProvider
from repoagent.providers.gemini import GeminiProvider
from repoagent.providers.openai_compatible import OpenAICompatibleProvider

_GEMINI_ALIASES = {"gemini", "google", "hosted"}
_OPENAI_ALIASES = {"openai", "openai_compatible", "openai-compatible", "ollama", "vllm", "local"}


def get_provider(
    provider_name: str | None = None,
    model_name: str | None = None,
    **kwargs: str,
) -> LLMProvider:
    """Return an LLMProvider instance based on name or the REPOAGENT_PROVIDER env var.

    Defaults to 'gemini' if REPOAGENT_PROVIDER is unset.
    """
    selected = (provider_name or os.environ.get("REPOAGENT_PROVIDER") or "gemini").strip().lower()

    if selected in _GEMINI_ALIASES:
        return GeminiProvider(model_name=model_name, **kwargs)
    elif selected in _OPENAI_ALIASES:
        return OpenAICompatibleProvider(model_name=model_name, **kwargs)
    else:
        raise ValueError(
            f"Unsupported LLM provider '{selected}'. "
            f"Supported providers: gemini (aliases: {sorted(_GEMINI_ALIASES)}), "
            f"openai_compatible (aliases: {sorted(_OPENAI_ALIASES)})."
        )
