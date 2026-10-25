"""Google ADK Code Reviewer Agent served over A2A.

Evaluates proposed code changes against the implementation Plan and
the deterministic TestReport (with workspace_hash) to produce a ReviewReport.
"""

from __future__ import annotations

import logging
from typing import Any

from a2a.types import AgentCard, AgentInterface, AgentSkill
from google.adk.agents import Agent

from repoagent.providers import get_provider
from repoagent.schemas import ReviewReport

logger = logging.getLogger(__name__)


def get_reviewer_agent_card(rpc_url: str = "http://localhost:8002/") -> AgentCard:
    """Build the official A2A AgentCard for the Code Reviewer agent."""
    return AgentCard(
        name="code-reviewer",
        description=(
            "ADK Code Reviewer that evaluates git diffs, deterministic TestReports, "
            "and Plans to produce a ReviewReport with verdict and findings."
        ),
        version="1.0.0",
        skills=[
            AgentSkill(
                id="review_diff",
                name="review_diff",
                description="Review git diffs and test results against requirements and plans",
                tags=["review", "quality", "security"],
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


def create_reviewer_agent(
    model: Any | None = None,
) -> Agent:
    """Create and configure the Google ADK Code Reviewer agent."""
    if model is None:
        provider = get_provider()
        model = provider.get_adk_llm()

    instruction = (
        "You are the Code Reviewer Agent.\n\n"
        "Your task is to review proposed code changes against the implementation Plan "
        "and the deterministic TestReport.\n"
        "Input provided to you includes:\n"
        "- The Plan (summary and intended changes)\n"
        "- The deterministic TestReport (including workspace_hash, exit code, and test log)\n"
        "- The real Git Diff computed from disk\n\n"
        "Review Guidelines:\n"
        "1. Check if the code correctly implements the plan and requirements.\n"
        "2. Look for potential security vulnerabilities, logic regressions, unhandled edge cases, "
        "or architectural violations.\n"
        "3. Deterministic tests already passed. Your verdict assesses code quality, "
        "safety, and requirement fulfillment.\n"
        "4. If the changes are acceptable and clean, set verdict to 'approve'.\n"
        "5. If there are critical defects, security issues, or missing requirements, set verdict "
        "to 'request_changes' and provide actionable items in 'findings'.\n\n"
        "Output MUST be valid JSON adhering strictly to the ReviewReport schema:\n"
        "{\n"
        '  "verdict": "approve" | "request_changes",\n'
        '  "findings": [\n'
        "    {\n"
        '      "severity": "critical" | "warning" | "info",\n'
        '      "file": "path/to/file.py",\n'
        '      "line": 10,\n'
        '      "message": "Description of the finding"\n'
        "    }\n"
        "  ],\n"
        '  "summary": "Overall evaluation summary"\n'
        "}"
    )

    agent = Agent(
        name="code_reviewer",
        description="Reviews git diffs and test reports against implementation plans.",
        model=model,
        instruction=instruction,
        tools=[],
        output_schema=ReviewReport,
    )
    return agent
