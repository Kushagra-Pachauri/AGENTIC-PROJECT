"""RepoAgent shared Pydantic v2 models.

Every piece of data exchanged between agents flows through these schemas.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(str, Enum):
    """Status of a RepoAgent run."""

    RUNNING = "running"
    NEEDS_HUMAN = "needs_human"
    BLOCKED = "blocked"
    DONE = "done"


class ReviewVerdict(str, Enum):
    """Verdict from the code review agent."""

    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"


class FileChange(BaseModel):
    """A single file change in a changeset."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(..., description="Relative file path in the repo")
    action: str = Field(..., description="create | modify | delete")
    diff: str = Field(default="", description="Unified diff content")


class PlannedFile(BaseModel):
    """A file that the plan intends to change."""

    model_config = ConfigDict(frozen=True)

    path: str
    reason: str


class Plan(BaseModel):
    """Execution plan produced by the Planner agent."""

    summary: str
    files_to_change: list[PlannedFile] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    test_strategy: str = Field(default="")


class ChangeSet(BaseModel):
    """Set of code changes produced by the Coding agent."""

    files: list[FileChange] = Field(default_factory=list)
    summary: str = Field(default="")


class TestReport(BaseModel):
    """Result of running tests in the sandbox."""

    __test__ = False

    command: str
    passed: bool
    exit_code: int
    failed_tests: list[str] = Field(default_factory=list)
    log_tail: str = Field(default="", description="Last N lines of test output")
    duration_s: float = Field(default=0.0, ge=0.0)


class ReviewFinding(BaseModel):
    """A single finding from the code review."""

    model_config = ConfigDict(frozen=True)

    severity: str = Field(..., description="critical | warning | info")
    file: str
    line: int = Field(default=0, ge=0)
    message: str


class ReviewReport(BaseModel):
    """Report from the Code Review agent."""

    verdict: ReviewVerdict
    findings: list[ReviewFinding] = Field(default_factory=list)
    summary: str = Field(default="")


class PRSummary(BaseModel):
    """Summary of the created GitHub Pull Request."""

    title: str
    body_markdown: str = Field(default="")
    url: str = Field(default="")
    branch: str


class RunState(BaseModel):
    """Central shared state for a single RepoAgent run.

    This is the single source of truth passed through the orchestrator flow.
    All agents read from and write to this state via Pydantic models.
    """

    model_config = ConfigDict(validate_assignment=True)

    # Identity
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])

    # Input
    requirement: str = Field(default="")
    repo_url: str = Field(default="")

    # Branch
    branch: str = Field(default="")

    # Analysis
    repo_map: str = Field(default="", description="Structural map of the repository")

    # Planning
    plan: Plan | None = None

    # Coding
    changes: ChangeSet | None = None

    # Testing
    test_reports: list[TestReport] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=1)

    # Review
    review: ReviewReport | None = None

    # PR
    pr: PRSummary | None = None

    # Status tracking
    status: RunStatus = Field(default=RunStatus.RUNNING)
    errors: list[str] = Field(default_factory=list)

    # Timestamps
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
