"""Smoke test for standalone Streamlit showcase deployment without backend or Docker."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Force demo mode
os.environ["REPOAGENT_DEMO_MODE"] = "true"
os.environ["REPOAGENT_API_URL"] = "http://invalid-host:9999"

from repoagent.ui.dashboard import (
    evaluate_all_stages,
    fetch_health,
    fetch_run_details,
    fetch_run_events,
    fetch_runs,
    generate_pipeline_html,
)


def test_standalone_showcase() -> None:
    print("1. Testing fetch_health in demo mode...")
    health = fetch_health(api_url="http://invalid-host:9999")
    assert health is not None
    assert health["mode"] == "public_showcase"
    print("   Health mode:", health["mode"])

    print("2. Testing fetch_runs fallback to showcase_data...")
    runs = fetch_runs(api_url="http://invalid-host:9999")
    assert len(runs) >= 2, f"Expected at least 2 runs, got {len(runs)}"
    run_ids = [r["run_id"] for r in runs]
    print("   Loaded runs:", run_ids)
    assert "0f04e14907d7" in run_ids, "Run 0f04e14907d7 must be present"

    print("3. Testing fetch_run_details for 0f04e14907d7...")
    run_data = fetch_run_details("0f04e14907d7", api_url="http://invalid-host:9999")
    assert run_data is not None
    assert run_data["status"] == "done"
    assert run_data.get("pr", {}).get("pull_number") == 7
    print("   PR URL:", run_data["pr"]["url"])

    print("4. Testing fetch_run_events...")
    events = fetch_run_events("0f04e14907d7", api_url="http://invalid-host:9999")
    assert len(events) > 0
    print("   Events count:", len(events))

    print("5. Testing evaluate_all_stages...")
    stages = evaluate_all_stages(run_data, events)
    assert len(stages) == 11
    completed_stages = [s["name"] for s in stages if s["status"] == "COMPLETED"]
    print("   Completed stages count:", len(completed_stages))
    assert "Authoritative DONE Gate" in [s["name"] for s in stages if s["status"] == "COMPLETED"]

    print("6. Testing generate_pipeline_html...")
    html_code = generate_pipeline_html(stages, run_data)
    assert "pipeline-node-card" in html_code
    assert "pull/7" in html_code or "PR Ready" in html_code
    print("   Pipeline HTML generation: SUCCESS")

    print("\nALL STANDALONE SHOWCASE TESTS PASSED!")


if __name__ == "__main__":
    test_standalone_showcase()
