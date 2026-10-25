"""Provider smoke test script against real API."""

from __future__ import annotations

from dotenv import load_dotenv

from repoagent.providers import get_provider

load_dotenv()

if __name__ == "__main__":
    p = get_provider()
    adk_model = p.get_adk_model()
    crewai_llm = p.get_crewai_llm()
    print(f"Provider: {p.provider_name} | CrewAI: {crewai_llm.model} | ADK: {adk_model}")

    print("Executing live API test call...", flush=True)
    response = crewai_llm.call(
        [{"role": "user", "content": "Respond strictly with: Provider smoke test passed."}]
    )
    print(f"Response: {response.strip()}")
