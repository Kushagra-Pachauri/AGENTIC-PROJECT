"""Hosted Google Gemini provider adapter for CrewAI and Google ADK."""

from __future__ import annotations

import os
from typing import Any

from crewai import LLM

from repoagent.providers.base import LLMProvider
from repoagent.providers.retry import retry_with_backoff


class GeminiProvider(LLMProvider):
    """Google Gemini provider adapter."""

    def __init__(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model_name = (
            model_name
            or os.environ.get("REPOAGENT_MODEL")
            or os.environ.get("GEMINI_MODEL")
            or "gemini-3.6-flash"
        )
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
        """Return a CrewAI LLM configured for Gemini with capped backoff retry."""
        llm = LLM(
            model=f"gemini/{self._model_name}",
            api_key=self._api_key or "dummy-key-for-init",
        )
        original_call = llm.call
        model_id = self._model_name

        def call_with_backoff(*args: Any, **kwargs: Any) -> Any:
            return retry_with_backoff(
                lambda: original_call(*args, **kwargs),
                model_name=model_id,
                max_attempts=4,
                base_delay=2.0,
                max_delay=30.0,
                jitter=True,
            )

        llm.call = call_with_backoff
        return llm

    def get_adk_model(self) -> str:
        """Return model identifier recognized by Google ADK."""
        return self._model_name
