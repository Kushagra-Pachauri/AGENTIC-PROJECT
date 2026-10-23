"""Hosted Google Gemini provider adapter for CrewAI and Google ADK."""

from __future__ import annotations

import os

from crewai import LLM

from repoagent.providers.base import LLMProvider


class GeminiProvider(LLMProvider):
    """Google Gemini provider adapter."""

    def __init__(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model_name = model_name or os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash"
        self._api_key = (
            api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
        )

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def api_key(self) -> str:
        return self._api_key

    def get_crewai_llm(self) -> LLM:
        """Return a CrewAI LLM configured for Gemini."""
        return LLM(
            model=f"gemini/{self._model_name}",
            api_key=self._api_key or "dummy-key-for-init",
        )

    def get_adk_model(self) -> str:
        """Return model identifier recognized by Google ADK."""
        return self._model_name
