#!/usr/bin/env python3
"""RepoAgent cross-platform startup launcher.

Usage:
    python start.py                     # Interactive menu
    python start.py --mode local        # Full local stack (Dashboard + API + Agents)
    python start.py --mode dashboard    # Streamlit dashboard only
    python start.py --mode api          # FastAPI backend only
    python start.py --mode agents       # ADK A2A agents only
    python start.py --mode docker       # Docker compose stack
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

# Locate Python in virtual environment if available
VENV_PYTHON = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
if not VENV_PYTHON.exists():
    VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def banner() -> None:
    print("=" * 65)
    print("  RepoAgent: Autonomous Requirement-to-PR Multi-Agent System")
    print(f"  Python executable: {PYTHON}")
    print("=" * 65)


def start_local(dashboard_port: int = 8501, api_port: int = 8000, no_browser: bool = False) -> None:
    banner()
    print("\n[*] Starting RepoAgent Local Stack...")
    print("    - Google ADK Planner  : http://localhost:8001")
    print("    - Google ADK Reviewer : http://localhost:8002")
    print(f"    - FastAPI Backend     : http://localhost:{api_port}")
    print(f"    - Streamlit Dashboard : http://localhost:{dashboard_port}\n")

    processes = []
    try:
        # 1. Start ADK A2A Agent Server
        print("[1/3] Starting ADK A2A Agents (Planner 8001, Reviewer 8002)...")
        agent_proc = subprocess.Popen(
            [
                PYTHON,
                "-m",
                "repoagent.agents.adk.server",
                "--host",
                "127.0.0.1",
                "--planner-port",
                "8001",
                "--reviewer-port",
                "8002",
                "--workspace",
                "demo_repo",
            ],
            cwd=str(ROOT_DIR),
        )
        processes.append(agent_proc)

        # 2. Start FastAPI Backend
        print(f"[2/3] Starting FastAPI Backend on port {api_port}...")
        api_proc = subprocess.Popen(
            [
                PYTHON,
                "-m",
                "uvicorn",
                "repoagent.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            cwd=str(ROOT_DIR),
        )
        processes.append(api_proc)

        time.sleep(2)

        if not no_browser:
            webbrowser.open(f"http://localhost:{dashboard_port}")

        # 3. Start Streamlit Dashboard (blocking foreground)
        print(f"[3/3] Starting Streamlit Dashboard on port {dashboard_port}...")
        print("\n>>> Press Ctrl+C in this console to stop all RepoAgent services cleanly <<<\n")
        subprocess.run(
            [
                PYTHON,
                "-m",
                "streamlit",
                "run",
                str(ROOT_DIR / "src" / "repoagent" / "ui" / "dashboard.py"),
                "--server.port",
                str(dashboard_port),
                "--server.headless",
                "false",
            ],
            cwd=str(ROOT_DIR),
            check=False,
        )

    except KeyboardInterrupt:
        print("\n[*] Shutting down...")
    finally:
        for p in processes:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        print("[✓] All RepoAgent services stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="RepoAgent Startup Launcher")
    parser.add_argument(
        "--mode",
        choices=["local", "dashboard", "api", "agents", "docker", "menu"],
        default="menu",
        help="Startup mode (default: menu)",
    )
    parser.add_argument("--dashboard-port", type=int, default=8501)
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")

    args = parser.parse_args()

    if args.mode == "local":
        start_local(args.dashboard_port, args.api_port, args.no_browser)
    elif args.mode == "dashboard":
        banner()
        if not args.no_browser:
            webbrowser.open(f"http://localhost:{args.dashboard_port}")
        subprocess.run(
            [
                PYTHON,
                "-m",
                "streamlit",
                "run",
                str(ROOT_DIR / "src" / "repoagent" / "ui" / "dashboard.py"),
                "--server.port",
                str(args.dashboard_port),
            ],
            cwd=str(ROOT_DIR),
        )
    elif args.mode == "api":
        banner()
        subprocess.run(
            [
                PYTHON,
                "-m",
                "uvicorn",
                "repoagent.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.api_port),
                "--reload",
            ],
            cwd=str(ROOT_DIR),
        )
    elif args.mode == "agents":
        banner()
        subprocess.run(
            [
                PYTHON,
                "-m",
                "repoagent.agents.adk.server",
                "--host",
                "127.0.0.1",
                "--planner-port",
                "8001",
                "--reviewer-port",
                "8002",
                "--workspace",
                "demo_repo",
            ],
            cwd=str(ROOT_DIR),
        )
    elif args.mode == "docker":
        banner()
        subprocess.run(["docker", "compose", "up"], cwd=str(ROOT_DIR))
    else:
        banner()
        print("\nPlease select a startup option:")
        print("  [1] Start Full Local Stack (Dashboard + API + ADK Agents) [RECOMMENDED]")
        print(f"  [2] Start Streamlit Dashboard Only (Port {args.dashboard_port})")
        print(f"  [3] Start FastAPI Backend Only (Port {args.api_port})")
        print("  [4] Start ADK A2A Agents Only (Planner 8001, Reviewer 8002)")
        print("  [5] Start Docker Compose Stack")
        print("  [6] Run Unit Test Suite (pytest)")
        print("  [7] Exit")

        try:
            choice = input("\nEnter selection (1-7) [Default: 1]: ").strip()
        except (KeyboardInterrupt, EOFError):
            return

        if not choice:
            choice = "1"

        if choice == "1":
            start_local(args.dashboard_port, args.api_port, args.no_browser)
        elif choice == "2":
            subprocess.run(
                [
                    PYTHON,
                    "-m",
                    "streamlit",
                    "run",
                    str(ROOT_DIR / "src" / "repoagent" / "ui" / "dashboard.py"),
                    "--server.port",
                    str(args.dashboard_port),
                ],
                cwd=str(ROOT_DIR),
            )
        elif choice == "3":
            subprocess.run(
                [
                    PYTHON,
                    "-m",
                    "uvicorn",
                    "repoagent.api.app:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                    "--reload",
                ],
                cwd=str(ROOT_DIR),
            )
        elif choice == "4":
            subprocess.run(
                [
                    PYTHON,
                    "-m",
                    "repoagent.agents.adk.server",
                    "--host",
                    "127.0.0.1",
                    "--planner-port",
                    "8001",
                    "--reviewer-port",
                    "8002",
                    "--workspace",
                    "demo_repo",
                ],
                cwd=str(ROOT_DIR),
            )
        elif choice == "5":
            subprocess.run(["docker", "compose", "up"], cwd=str(ROOT_DIR))
        elif choice == "6":
            subprocess.run([PYTHON, "-m", "pytest"], cwd=str(ROOT_DIR))


if __name__ == "__main__":
    main()
