"""A2A Server exposing Google ADK Planner and Reviewer agents.

Provides:
- create_planner_app: Starlette app for Planner over A2A
- create_reviewer_app: Starlette app for Reviewer over A2A
- CLI entrypoints to run Planner (default port 8001), Reviewer (default port 8002), or both
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import uvicorn
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from starlette.applications import Starlette

from repoagent.agents.adk.planner import create_planner_agent, get_planner_agent_card
from repoagent.agents.adk.reviewer import create_reviewer_agent, get_reviewer_agent_card

logger = logging.getLogger(__name__)


def get_public_url(port: int, host: str = "127.0.0.1") -> str:
    """Resolve public URL for agent card. Never returns 0.0.0.0."""
    override = os.environ.get("REPOAGENT_A2A_PUBLIC_URL")
    if override:
        return override.rstrip("/") + "/"
    # Ensure agent card URL is always a reachable host, never 0.0.0.0 or ::
    card_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{card_host}:{port}/"


def create_planner_app(
    host: str = "127.0.0.1",
    port: int = 8001,
    workspace: Path | str | None = None,
    model: Any | None = None,
    public_url: str | None = None,
) -> Starlette:
    """Create Starlette A2A application for the Repo Analyst & Planner agent."""
    agent = create_planner_agent(workspace=workspace, model=model)
    rpc_url = public_url or get_public_url(port=port, host=host)
    card = get_planner_agent_card(rpc_url=rpc_url)
    app = to_a2a(
        agent,
        host=host,
        port=port,
        agent_card=card,
    )
    return app


def create_reviewer_app(
    host: str = "127.0.0.1",
    port: int = 8002,
    model: Any | None = None,
    public_url: str | None = None,
) -> Starlette:
    """Create Starlette A2A application for the Code Reviewer agent."""
    agent = create_reviewer_agent(model=model)
    rpc_url = public_url or get_public_url(port=port, host=host)
    card = get_reviewer_agent_card(rpc_url=rpc_url)
    app = to_a2a(
        agent,
        host=host,
        port=port,
        agent_card=card,
    )
    return app


async def run_both_servers(
    host: str = "127.0.0.1",
    planner_port: int = 8001,
    reviewer_port: int = 8002,
    workspace: Path | str | None = None,
) -> None:
    """Run both Planner and Reviewer A2A servers concurrently."""
    app_planner = create_planner_app(host=host, port=planner_port, workspace=workspace)
    app_reviewer = create_reviewer_app(host=host, port=reviewer_port)

    config_planner = uvicorn.Config(
        app_planner, host=host, port=planner_port, log_level="info", lifespan="on"
    )
    config_reviewer = uvicorn.Config(
        app_reviewer, host=host, port=reviewer_port, log_level="info", lifespan="on"
    )

    server_planner = uvicorn.Server(config_planner)
    server_reviewer = uvicorn.Server(config_reviewer)

    logger.info("Starting A2A Planner on %s:%d", host, planner_port)
    logger.info("Starting A2A Reviewer on %s:%d", host, reviewer_port)

    await asyncio.gather(server_planner.serve(), server_reviewer.serve())


def main() -> None:
    default_host = os.environ.get("REPOAGENT_A2A_HOST", "127.0.0.1")
    parser = argparse.ArgumentParser(description="Start RepoAgent A2A Agent Servers")
    parser.add_argument(
        "--agent",
        choices=["planner", "reviewer", "all"],
        default="all",
        help="Agent to serve (default: all)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=default_host,
        help=f"Host interface to bind (default: {default_host})",
    )
    parser.add_argument(
        "--planner-port",
        type=int,
        default=int(os.environ.get("REPOAGENT_PLANNER_PORT", 8001)),
        help="Port for Planner agent (default: 8001)",
    )
    parser.add_argument(
        "--reviewer-port",
        type=int,
        default=int(os.environ.get("REPOAGENT_REVIEWER_PORT", 8002)),
        help="Port for Reviewer agent (default: 8002)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Single agent port override (used when --agent is planner or reviewer)",
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default=os.environ.get("REPOAGENT_WORKSPACE", "demo_repo"),
        help="Workspace directory for repo-reader tools (default: demo_repo)",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.agent == "planner":
        port = args.port or args.planner_port
        app = create_planner_app(host=args.host, port=port, workspace=args.workspace)
        uvicorn.run(app, host=args.host, port=port, lifespan="on")
    elif args.agent == "reviewer":
        port = args.port or args.reviewer_port
        app = create_reviewer_app(host=args.host, port=port)
        uvicorn.run(app, host=args.host, port=port, lifespan="on")
    else:
        asyncio.run(
            run_both_servers(
                host=args.host,
                planner_port=args.planner_port,
                reviewer_port=args.reviewer_port,
                workspace=args.workspace,
            )
        )


if __name__ == "__main__":
    main()
