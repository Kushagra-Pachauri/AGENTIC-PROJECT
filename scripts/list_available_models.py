"""List models available to the current Gemini API key."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from google import genai

load_dotenv()


def main() -> None:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("[ERROR] No GEMINI_API_KEY or GOOGLE_API_KEY found in environment or .env")
        return

    client = genai.Client(api_key=api_key)
    print("=== Fetching Available Models ===")
    models = list(client.models.list())
    generation_models = []
    for m in models:
        actions = getattr(m, "supported_actions", []) or []
        if "generateContent" in actions:
            clean_name = m.name.replace("models/", "")
            generation_models.append(clean_name)
            print(f"- {clean_name} (Actions: {actions})")

    print(f"\nTotal generation models found: {len(generation_models)}")

    # Test top candidates for quick response
    candidates = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-3.8-flash",
        "gemini-3.5-flash",
        "gemini-3.7-flash",
        "gemini-flash-latest",
    ]
    print("\n=== Testing Candidate Models ===")
    for c in candidates:
        if c in generation_models:
            print(f"Testing {c}...", end=" ", flush=True)
            try:
                res = client.models.generate_content(
                    model=c,
                    contents="Reply with OK",
                )
                print(f"SUCCESS -> '{res.text.strip()}'")
            except Exception as e:
                err_msg = str(e)
                if "404" in err_msg or "not found" in err_msg.lower():
                    print("FAILED (404 Retired/Unavailable)")
                elif "429" in err_msg or "quota" in err_msg.lower():
                    print(f"FAILED (429 Quota/Rate Limit): {err_msg[:80]}")
                else:
                    print(f"FAILED ({err_msg[:80]})")


if __name__ == "__main__":
    main()
