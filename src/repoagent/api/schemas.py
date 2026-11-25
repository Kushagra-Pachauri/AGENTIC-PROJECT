"""API request and response schemas for the RepoAgent FastAPI backend."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from repoagent.schemas import PRSummary, ReviewReport, RunStatus, TestReport


class RunCreateRequest(BaseModel):
    """Payload to initiate a new RepoAgent run."""

    repo_url: str = Field(
        default="demo_repo",
        description="Path or URL to the target repository",
    )
    requirement: str = Field(
        ...,
        min_length=3,
        description="Natural-language feature or fix requirement",
    )
    provider: str | None = Field(
        default=None,
        description="LLM provider name (e.g. 'ollama', 'gemini')",
    )
    max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum correction retry iterations",
    )
    simulate_failures: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Number of test failures to simulate for testing",
    )
    create_pr: bool = Field(
        default=False,
        description="Whether to open a real GitHub PR if verification passes",
    )
    use_live_agents: bool = Field(
        default=False,
        description="Whether to execute live LLM agents (default: False for deterministic runs)",
    )
    is_replay: bool = Field(
        default=False,
        description="Whether to execute in replay benchmark mode",
    )
    sync: bool = Field(
        default=False,
        description="Whether to execute synchronously before returning response",
    )

    @field_validator("requirement")
    @classmethod
    def _validate_requirement_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Requirement cannot be empty or blank")
        return stripped


class RunCreateResponse(BaseModel):
    """Response returned upon creating a run."""

    run_id: str
    status: RunStatus
    requirement: str
    repo_url: str
    created_at: str
    message: str


class RunResponse(BaseModel):
    """Detailed status representation of a run."""

    run_id: str
    status: RunStatus
    requirement: str
    repo_url: str
    branch: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_s: float | None = None
    retry_count: int = 0
    max_retries: int = 3
    test_reports: list[TestReport] = Field(default_factory=list)
    review: ReviewReport | None = None
    pr: PRSummary | None = None
    errors: list[str] = Field(default_factory=list)
    rejected_changes_count: int = 0
    trace_url: str | None = None
    is_active: bool = False
    is_stale: bool = False


class RunSummary(BaseModel):
    """Compact summary of a run for listing and dashboard overviews."""

    run_id: str
    status: RunStatus
    requirement: str
    repo_url: str
    started_at: str | None = None
    finished_at: str | None = None
    pr_url: str | None = None
    error_count: int = 0
    is_active: bool = False
    is_stale: bool = False


class RunEventsResponse(BaseModel):
    """Paginated events for a specific run."""

    run_id: str
    total_events: int
    offset: int
    limit: int
    events: list[dict[str, Any]]


class HealthResponse(BaseModel):
    """Application health and diagnostic status."""

    status: str = "healthy"
    version: str = "0.1.0"
    runs_dir_writable: bool = True
    langfuse_enabled: bool = False
    timestamp: str


_RUN_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")


def validate_run_id_safe(run_id: str) -> str:
    """Validate that run_id contains only alphanumeric characters, dashes, and underscores."""
    clean = run_id.strip() if run_id else ""
    if not clean or not _RUN_ID_REGEX.match(clean) or clean in (".", ".."):
        raise ValueError(
            f"Invalid run_id: '{run_id}'. Must be alphanumeric without path traversal."
        )
    return clean
