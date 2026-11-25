"""Google ADK Repo Analyst and Planner Agent served over A2A.

Uses repo-reader MCP tools to explore repository layout and source code,
producing a structural repo_map and an execution Plan matching Pydantic schemas.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from a2a.types import AgentCard, AgentInterface, AgentSkill
from google.adk.agents import Agent

from repoagent.mcp_servers import repo_reader
from repoagent.monitoring import attach_adk_callbacks
from repoagent.providers import get_provider
from repoagent.schemas import PlannerOutput

logger = logging.getLogger(__name__)


def list_repository_tree(path: str = ".", max_depth: int = 4) -> str:
    """List the directory tree of the target repository."""
    return repo_reader.list_tree(path=path, max_depth=max_depth)


def read_repository_file(path: str, offset: int = 0, limit: int = 200) -> str:
    """Read contents of a file in the target repository."""
    return repo_reader.read_file(path=path, offset=offset, limit=limit)


def search_repository_code(pattern: str, path: str = ".") -> str:
    """Search for regex pattern across files in the target repository."""
    return repo_reader.search_code(pattern=pattern, path=path)


def get_planner_agent_card(rpc_url: str = "http://localhost:8001/") -> AgentCard:
    """Build the official A2A AgentCard for the Repo Analyst and Planner agent."""
    return AgentCard(
        name="repo-planner",
        description=(
            "ADK Repo Analyst and Planner that analyzes repository structure "
            "using repo-reader MCP tools and generates structured implementation plans."
        ),
        version="1.0.0",
        skills=[
            AgentSkill(
                id="analyze_repo",
                name="analyze_repo",
                description="Analyze repository architecture and produce a structural repo_map",
                tags=["analysis", "repo-reader"],
            ),
            AgentSkill(
                id="generate_plan",
                name="generate_plan",
                description="Generate a structured implementation Plan for a given requirement",
                tags=["planning", "architecture"],
            ),
        ],
        supported_interfaces=[
            AgentInterface(
                url=rpc_url,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["application/json"],
    )


def create_planner_agent(
    workspace: Path | str | None = None,
    model: Any | None = None,
    run_id: str | None = None,
) -> Agent:
    """Create and configure the Google ADK Repo Analyst / Planner agent."""
    if workspace is not None:
        ws_path = Path(workspace).resolve()
        repo_reader._set_workspace(str(ws_path))

    if model is None:
        provider = get_provider()
        model = provider.get_adk_llm()

    instruction = (
        "You are the Repo Analyst and Planner Agent.\n\n"
        "Your objective:\n"
        "1. Analyze the repository files, packages, and architecture.\n"
        "2. Formulate a concise, clear 'repo_map' outlining services and components.\n"
        "3. Produce a structured implementation 'plan' containing:\n"
        "   - summary: high-level summary of the solution\n"
        "   - files_to_change: list of objects with 'path' and 'reason'\n"
        "   - steps: ordered list of implementation steps\n"
        "   - risks: key risks and considerations\n"
        "   - test_strategy: description of test verification\n\n"
        "TOOL CALL INSTRUCTIONS:\n"
        "When calling read tools (such as read_file or read_repository_file), use strictly: "
        "'path' (string), 'offset' (int, default 0), and 'limit' (int, default 200). "
        "NEVER use 'line_start' or 'line_end'.\n\n"
        "Output MUST be valid JSON adhering strictly to the PlannerOutput schema:\n"
        "{\n"
        '  "repo_map": "...",\n'
        '  "plan": {\n'
        '    "summary": "...",\n'
        '    "files_to_change": [{"path": "...", "reason": "..."}],\n'
        '    "steps": ["..."],\n'
        '    "risks": ["..."],\n'
        '    "test_strategy": "..."\n'
        "  }\n"
        "}"
    )

    agent = Agent(
        name="repo_planner",
        description="Analyzes repository architecture and generates implementation plans.",
        model=model,
        instruction=instruction,
        tools=[],
        output_schema=PlannerOutput,
    )
    attach_adk_callbacks(agent, run_id=run_id, agent_name="repo_planner", stage="analyze")
    return agent
