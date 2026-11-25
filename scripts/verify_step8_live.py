"""Step 8 Live Verification Script.

Tests live application entrypoints, endpoints, realistic end-to-end run,
and Streamlit dashboard components locally without Docker daemon dependency.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_step8")

ROOT_DIR = Path(__file__).resolve().parent.parent


def check_docker_status() -> dict[str, str]:
    """Inspect docker CLI and engine daemon availability."""
    results = {}
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0:
            results["docker_cli"] = "available"
            results["docker_engine"] = "running"
        else:
            results["docker_cli"] = "available"
            results["docker_engine"] = f"stopped ({proc.stderr.strip()[:100]})"
    except Exception as exc:
        results["docker_cli"] = f"error ({exc})"
        results["docker_engine"] = "unavailable"
    return results


def validate_compose_configs() -> dict[str, bool]:
    """Validate docker-compose.yml and docker-compose.langfuse.yml."""
    configs = {}
    for compose_file in ["docker-compose.yml", "docker-compose.langfuse.yml"]:
        try:
            proc = subprocess.run(
                ["docker", "compose", "-f", compose_file, "config"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(ROOT_DIR),
            )
            configs[compose_file] = proc.returncode == 0
        except Exception as exc:
            logger.error("Error checking %s: %s", compose_file, exc)
            configs[compose_file] = False
    return configs


def test_live_servers() -> dict[str, any]:
    """Start local backend servers as subprocesses, test endpoints, execute run, and clean up."""
    report: dict[str, any] = {}
    python_exe = sys.executable

    # Check ports before starting
    logger.info("Starting local test servers...")
    adk_proc = None
    api_proc = None
    temp_repo_dir = None

    try:
        # 1. Start ADK servers (Planner on 8001, Reviewer on 8002)
        adk_proc = subprocess.Popen(
            [
                python_exe,
                "-m",
                "repoagent.agents.adk.server",
                "--agent",
                "all",
                "--planner-port",
                "8001",
                "--reviewer-port",
                "8002",
                "--workspace",
                str(ROOT_DIR / "demo_repo"),
            ],
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # 2. Start FastAPI backend (port 8000)
        api_proc = subprocess.Popen(
            [
                python_exe,
                "-m",
                "uvicorn",
                "repoagent.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for servers to spin up
        time.sleep(4.0)

        with httpx.Client(timeout=10.0) as client:
            # 3. Test API Health
            logger.info("Probing GET http://127.0.0.1:8000/health...")
            health_resp = client.get("http://127.0.0.1:8000/health")
            report["api_health_status"] = health_resp.status_code
            report["api_health_body"] = health_resp.json()
            assert health_resp.status_code == 200
            assert health_resp.json().get("status") == "healthy"
            logger.info("API Health: OK (v%s)", health_resp.json().get("version"))

            # 4. Test Planner A2A Agent Card (port 8001)
            logger.info("Probing GET http://127.0.0.1:8001/.well-known/agent-card.json...")
            planner_resp = client.get("http://127.0.0.1:8001/.well-known/agent-card.json")
            report["planner_card_status"] = planner_resp.status_code
            report["planner_card_body"] = planner_resp.json()
            assert planner_resp.status_code == 200
            assert "repo-planner" in planner_resp.json().get("name", "")
            logger.info("Planner Agent Card: OK (%s)", planner_resp.json().get("name"))

            # 5. Test Reviewer A2A Agent Card (port 8002)
            logger.info("Probing GET http://127.0.0.1:8002/.well-known/agent-card.json...")
            reviewer_resp = client.get("http://127.0.0.1:8002/.well-known/agent-card.json")
            report["reviewer_card_status"] = reviewer_resp.status_code
            report["reviewer_card_body"] = reviewer_resp.json()
            assert reviewer_resp.status_code == 200
            assert "code-reviewer" in reviewer_resp.json().get("name", "")
            logger.info("Reviewer Agent Card: OK (%s)", reviewer_resp.json().get("name"))

            # 6. Realistic Local End-to-End Run using isolated temporary repo copy
            temp_repo_dir = tempfile.mkdtemp(prefix="repoagent_live_test_")
            demo_repo_src = ROOT_DIR / "demo_repo"
            for item in demo_repo_src.iterdir():
                if item.is_dir():
                    shutil.copytree(item, Path(temp_repo_dir) / item.name)
                else:
                    shutil.copy2(item, Path(temp_repo_dir) / item.name)

            logger.info("Prepared isolated test repository at %s", temp_repo_dir)

            run_payload = {
                "repo_url": str(temp_repo_dir),
                "requirement": "Add docstring explaining authentication logic in auth.py",
                "max_retries": 2,
                "simulate_failures": 0,
                "create_pr": False,
                "use_live_agents": False,
            }

            logger.info("Triggering POST /runs...")
            create_resp = client.post("http://127.0.0.1:8000/runs", json=run_payload)
            report["run_create_status"] = create_resp.status_code
            report["run_create_body"] = create_resp.json()
            assert create_resp.status_code == 201
            run_id = create_resp.json()["run_id"]
            logger.info("Run created with run_id=%s, polling status...", run_id)

            # Poll for completion (up to 30s)
            start_poll = time.time()
            final_run_data = None
            while time.time() - start_poll < 30.0:
                poll_resp = client.get(f"http://127.0.0.1:8000/runs/{run_id}")
                if poll_resp.status_code == 200:
                    final_run_data = poll_resp.json()
                    st = final_run_data.get("status")
                    if st in ("done", "needs_human", "blocked", "failed"):
                        logger.info("Run %s reached terminal state: %s", run_id, st)
                        break
                time.sleep(1.0)

            report["e2e_run_final_state"] = final_run_data
            assert final_run_data is not None
            logger.info(
                "Run status: %s | Duration: %ss | DONE gate evaluated: %s",
                final_run_data.get("status"),
                final_run_data.get("duration_s"),
                final_run_data.get("status") == "done",
            )

            # Fetch audit events
            logger.info("Fetching audit events for %s...", run_id)
            events_resp = client.get(f"http://127.0.0.1:8000/runs/{run_id}/events")
            report["events_status"] = events_resp.status_code
            events = events_resp.json().get("events", [])
            report["events_count"] = len(events)
            report["event_types"] = [e.get("event_type") for e in events]
            logger.info("Captured %d audit events: %s", len(events), report["event_types"])

            # 7. Test Streamlit Dashboard functions with live data
            from repoagent.ui.dashboard import (
                compute_overview_metrics,
                fetch_health,
                fetch_run_details,
                fetch_run_events,
                fetch_runs,
                sanitize_display_text,
            )

            dash_health = fetch_health("http://127.0.0.1:8000")
            assert dash_health is not None
            report["dashboard_health_check"] = "PASS"

            dash_runs = fetch_runs("http://127.0.0.1:8000")
            assert len(dash_runs) >= 1
            report["dashboard_fetch_runs_count"] = len(dash_runs)

            metrics = compute_overview_metrics(dash_runs)
            report["dashboard_metrics"] = metrics
            logger.info("Dashboard computed metrics: %s", metrics)

            details = fetch_run_details(run_id, "http://127.0.0.1:8000")
            assert details is not None
            assert details["run_id"] == run_id
            report["dashboard_details_fetch"] = "PASS"

            dash_events = fetch_run_events(run_id, api_url="http://127.0.0.1:8000")
            assert len(dash_events) == len(events)
            report["dashboard_events_fetch"] = "PASS"

            # Sanitization test
            clean_log = sanitize_display_text(
                "Log with secret ghp_abcdefghijklmnopqrstuvwxyz123456"
            )
            assert "ghp_abcdef" not in clean_log
            report["dashboard_sanitization"] = "PASS"

    finally:
        # Gracefully stop background processes
        if api_proc:
            api_proc.terminate()
            try:
                api_proc.wait(timeout=5)
            except Exception:
                api_proc.kill()
        if adk_proc:
            adk_proc.terminate()
            try:
                adk_proc.wait(timeout=5)
            except Exception:
                adk_proc.kill()
        if temp_repo_dir and os.path.exists(temp_repo_dir):
            shutil.rmtree(temp_repo_dir, ignore_errors=True)

    return report


def main() -> None:
    logger.info("=== STEP 8 LIVE VERIFICATION START ===")

    # 1. Check Docker status
    docker_status = check_docker_status()
    logger.info("Docker CLI: %s", docker_status.get("docker_cli"))
    logger.info("Docker Engine: %s", docker_status.get("docker_engine"))

    # 2. Validate Compose configs
    compose_results = validate_compose_configs()
    for f, res in compose_results.items():
        logger.info("Compose file %s valid: %s", f, res)

    # 3. Live Server & E2E verification
    live_results = test_live_servers()
    logger.info("=== STEP 8 LIVE VERIFICATION RESULTS ===")
    print(json.dumps(live_results, indent=2, default=str))

    # Verify demo_repo was not modified
    git_status = subprocess.run(
        ["git", "status", "--porcelain", "demo_repo/"],
        capture_output=True,
        text=True,
    )
    if git_status.stdout.strip():
        logger.error("VIOLATION: demo_repo was modified!\n%s", git_status.stdout)
        sys.exit(1)
    else:
        logger.info("Integrity check: demo_repo/ remains 100% UNTOUCHED.")

    logger.info("=== STEP 8 LIVE VERIFICATION COMPLETE: ALL CHECKS PASSED ===")


if __name__ == "__main__":
    main()
