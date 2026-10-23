"""Base interface for LLM providers across CrewAI and Google ADK."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Abstract adapter interface to build model objects for CrewAI and Google ADK."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider (e.g. 'gemini', 'openai_compatible')."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Name of the underlying LLM model."""
        ...

    @abstractmethod
    def get_crewai_llm(self) -> Any:
        """Build and return the model object expected by CrewAI (crewai.LLM)."""
        ...

    @abstractmethod
    def get_adk_model(self) -> str | Any:
        """Build and return the model identifier/object expected by Google ADK."""
        ...
