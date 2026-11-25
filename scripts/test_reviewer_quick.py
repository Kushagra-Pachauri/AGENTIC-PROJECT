import os, dotenv, openai

dotenv.load_dotenv()
groq_key = os.environ.get("GROQ_API_KEY")

print("1. Testing Groq...")
try:
    client = openai.OpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1")
    resp = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[{"role": "user", "content": 'Return strictly JSON: {"verdict": "approve", "findings": [], "summary": "Looks good."}'}],
        max_tokens=60,
    )
    print("Groq response:", resp.choices[0].message.content)
except Exception as e:
    print("Groq error:", e)

print("\n2. Testing Ollama with max_tokens=150...")
try:
    client_ol = openai.OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
    resp_ol = client_ol.chat.completions.create(
        model="qwen2.5-coder:3b",
        messages=[{"role": "user", "content": 'Return strictly JSON in 1 sentence: {"verdict": "approve", "findings": [], "summary": "Looks good."}'}],
        max_tokens=60,
    )
    print("Ollama response:", resp_ol.choices[0].message.content)
except Exception as e:
    print("Ollama error:", e)
