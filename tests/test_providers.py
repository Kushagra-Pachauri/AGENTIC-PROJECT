"""Tests for LLMProvider adapters (Gemini and OpenAI-compatible)."""

from __future__ import annotations

import pytest
from crewai.llms.base_llm import BaseLLM

from repoagent.providers import (
    GeminiProvider,
    LLMProvider,
    OpenAICompatibleProvider,
    get_provider,
)


class TestProviders:
    """Test suite for LLM provider adapters."""

    def test_gemini_provider_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
        monkeypatch.delenv("GEMINI_MODEL", raising=False)

        provider = GeminiProvider()
        assert isinstance(provider, LLMProvider)
        assert provider.provider_name == "gemini"
        assert provider.model_name == "gemini-2.0-flash"
        assert provider.api_key == "test-google-key"

        crewai_llm = provider.get_crewai_llm()
        assert isinstance(crewai_llm, BaseLLM)
        assert "gemini" in crewai_llm.model

        adk_model = provider.get_adk_model()
        assert isinstance(adk_model, str)
        assert "gemini-2.0-flash" in adk_model

    def test_gemini_provider_custom_model(self) -> None:
        provider = GeminiProvider(model_name="gemini-1.5-pro", api_key="custom-key")
        assert provider.model_name == "gemini-1.5-pro"
        assert provider.api_key == "custom-key"
        assert "gemini-1.5-pro" in provider.get_adk_model()

    def test_openai_compatible_provider_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        provider = OpenAICompatibleProvider()
        assert isinstance(provider, LLMProvider)
        assert provider.provider_name == "openai_compatible"
        assert provider.model_name == "qwen2.5-coder"
        assert "localhost:11434" in provider.base_url

        crewai_llm = provider.get_crewai_llm()
        assert isinstance(crewai_llm, BaseLLM)
        assert "qwen2.5-coder" in crewai_llm.model
        assert crewai_llm.base_url == provider.base_url

        adk_model = provider.get_adk_model()
        assert isinstance(adk_model, str)
        assert adk_model == "openai/qwen2.5-coder"

    def test_openai_compatible_custom_endpoint(self) -> None:
        provider = OpenAICompatibleProvider(
            model_name="deepseek-coder",
            base_url="http://vllm-host:8000/v1",
            api_key="vllm-token",
        )
        assert provider.model_name == "deepseek-coder"
        assert provider.base_url == "http://vllm-host:8000/v1"
        assert provider.api_key == "vllm-token"
        assert provider.get_adk_model() == "openai/deepseek-coder"

    def test_factory_selection_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPOAGENT_PROVIDER", "gemini")
        provider1 = get_provider()
        assert isinstance(provider1, GeminiProvider)

        monkeypatch.setenv("REPOAGENT_PROVIDER", "openai_compatible")
        provider2 = get_provider()
        assert isinstance(provider2, OpenAICompatibleProvider)

        monkeypatch.setenv("REPOAGENT_PROVIDER", "ollama")
        provider3 = get_provider()
        assert isinstance(provider3, OpenAICompatibleProvider)

    def test_factory_selection_via_argument(self) -> None:
        p_gemini = get_provider("gemini")
        assert isinstance(p_gemini, GeminiProvider)

        p_openai = get_provider("openai")
        assert isinstance(p_openai, OpenAICompatibleProvider)

    def test_factory_invalid_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported LLM provider 'invalid_provider'"):
            get_provider("invalid_provider")
