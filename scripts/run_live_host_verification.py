"""Host-based live pipeline verification script using Ollama + Qwen2.5-Coder."""

import os
import sys
import threading
import time
from pathlib import Path

# Ensure src is in sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Set Ollama provider configuration
MODEL_NAME = "qwen2.5-coder:3b"
OLLAMA_URL = "http://localhost:11434/v1"

os.environ["REPOAGENT_PROVIDER"] = "ollama"
os.environ["REPOAGENT_MODEL"] = MODEL_NAME
os.environ["OPENAI_MODEL"] = MODEL_NAME
os.environ["LOCAL_MODEL"] = MODEL_NAME
os.environ["OPENAI_BASE_URL"] = OLLAMA_URL
os.environ["REPOAGENT_PLANNER_URL"] = "http://127.0.0.1:8011"
os.environ["REPOAGENT_REVIEWER_URL"] = "http://127.0.0.1:8012"

import uvicorn
from repoagent.providers.openai_compatible import OpenAICompatibleProvider
from repoagent.agents.adk.server import create_planner_app, create_reviewer_app
from repoagent.run import main

def start_background_a2a_servers():
    """Start ADK Planner and Reviewer servers bound to Ollama on ports 8011 and 8012."""
    provider = OpenAICompatibleProvider(model_name=MODEL_NAME, base_url=OLLAMA_URL)
    adk_model = provider.get_adk_llm()

    planner_app = create_planner_app(host="127.0.0.1", port=8011, workspace="demo_repo", model=adk_model)
    planner_cfg = uvicorn.Config(planner_app, host="127.0.0.1", port=8011, log_level="warning")
    planner_srv = uvicorn.Server(planner_cfg)
    t_p = threading.Thread(target=planner_srv.run, daemon=True)
    t_p.start()

    reviewer_app = create_reviewer_app(host="127.0.0.1", port=8012, model=adk_model)
    reviewer_cfg = uvicorn.Config(reviewer_app, host="127.0.0.1", port=8012, log_level="warning")
    reviewer_srv = uvicorn.Server(reviewer_cfg)
    t_r = threading.Thread(target=reviewer_srv.run, daemon=True)
    t_r.start()

    # Allow servers to bind
    time.sleep(1.5)
    print(f"[*] Local ADK Planner (8011) and Reviewer (8012) online with {MODEL_NAME}.")

def run_verification():
    start_background_a2a_servers()

    requirement = (
        "In src/notes_app/notes.py, add the method to NotesService:\n"
        "    def search_notes_by_title(self, user_id: str, query: str) -> list[Note]:\n"
        "        query = query.lower()\n"
        "        return [n for n in self.list_notes(user_id) if query in n.title.lower()]\n\n"
        "In tests/test_notes.py, add the unit test to TestNotes class:\n"
        "    def test_search_notes_by_title(self, app_with_user: NotesApp) -> None:\n"
        "        user = app_with_user.auth.get_user('alice')\n"
        "        assert user is not None\n"
        "        app_with_user.create_note('alice', 'Shopping', 'Buy milk')\n"
        "        app_with_user.create_note('alice', 'Grocery List', 'Wash lettuce')\n"
        "        app_with_user.create_note('alice', 'Homework', 'Study Python')\n"
        "        notes = app_with_user.notes.search_notes_by_title(user.id, 'shopping')\n"
        "        assert len(notes) == 1\n"
        "        assert notes[0].title == 'Shopping'\n"
        "        notes = app_with_user.notes.search_notes_by_title(user.id, 'Grocery list')\n"
        "        assert len(notes) == 1\n"
        "        assert notes[0].title == 'Grocery List'\n"
        "        notes = app_with_user.notes.search_notes_by_title(user.id, 'nonexistent')\n"
        "        assert len(notes) == 0\n"
    )

    args = [
        "--repo", "demo_repo",
        "--requirement", requirement,
        "--provider", "ollama",
        "--model", MODEL_NAME,
        "--max-retries", "2",
        "--live",
    ]

    print(f"[*] Starting host-based live agent verification run with Ollama ({MODEL_NAME})...")
    return main(args)

if __name__ == "__main__":
    sys.exit(run_verification())
