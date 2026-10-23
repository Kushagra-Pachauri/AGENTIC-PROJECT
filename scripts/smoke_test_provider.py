"""Provider smoke test script."""

from __future__ import annotations

from repoagent.providers import get_provider

if __name__ == "__main__":
    p = get_provider()
    adk_model = p.get_adk_model()
    crewai_model = p.get_crewai_llm().model
    print(f"Provider: {p.provider_name} | CrewAI: {crewai_model} | ADK: {adk_model}")
