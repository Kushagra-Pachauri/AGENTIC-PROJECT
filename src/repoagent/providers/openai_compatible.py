"""OpenAI-compatible endpoint provider adapter (Ollama, vLLM, self-hosted, etc.)."""

from __future__ import annotations

import os
from typing import Any

from crewai import LLM
from repoagent.providers.base import LLMProvider


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible provider for local/self-hosted coding models (e.g. Qwen2.5-Coder)."""

    def __init__(
        self,
        model_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model_name = (
            model_name
            or os.environ.get("REPOAGENT_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or os.environ.get("LOCAL_MODEL")
            or "qwen2.5-coder"
        )
        self._base_url = (
            base_url
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE")
            or "http://localhost:11434/v1"
        )
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY") or "ollama"

        # Ensure Google ADK LiteLLM routes to endpoint without overwriting explicit env vars
        os.environ.setdefault("OPENAI_BASE_URL", self._base_url)
        os.environ.setdefault("OPENAI_API_BASE", self._base_url)

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    def get_crewai_llm(self) -> LLM:
        """Return a CrewAI LLM configured for the OpenAI-compatible endpoint."""
        return LLM(
            model=f"openai/{self._model_name}",
            base_url=self._base_url,
            api_key=self._api_key,
            extra_body={"options": {"num_ctx": 8192}},
        )

    def get_adk_model(self) -> str:
        """Return model identifier recognized by Google ADK."""
        return f"openai/{self._model_name}"

    def get_adk_llm(self) -> Any:
        """Return an initialized Google ADK OpenAILlm configured for this endpoint."""
        from google.adk.integrations.openai._openai_llm import OpenAILlm

        return OpenAILlm(
            model=self._model_name,
            base_url=self._base_url,
            api_key=self._api_key or "ollama",
        )
