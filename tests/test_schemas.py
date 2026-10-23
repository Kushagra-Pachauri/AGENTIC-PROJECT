"""Tests for RepoAgent Pydantic schemas."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from repoagent.schemas import (
    ChangeSet,
    FileChange,
    Plan,
    PlannedFile,
    PRSummary,
    ReviewFinding,
    ReviewReport,
    ReviewVerdict,
    RunState,
    RunStatus,
    TestReport,
)


class TestRunState:
    """Tests for the central RunState model."""

    def test_default_creation(self) -> None:
        state = RunState()
        assert state.run_id  # auto-generated
        assert state.status == RunStatus.RUNNING
        assert state.retry_count == 0
        assert state.max_retries == 3
        assert state.test_reports == []
        assert state.errors == []
        assert state.plan is None
        assert state.changes is None
        assert state.review is None
        assert state.pr is None
        assert isinstance(state.started_at, datetime)
        assert state.finished_at is None

    def test_json_round_trip(self) -> None:
        state = RunState(
            requirement="Add password reset",
            repo_url="https://github.com/test/repo",
            branch="repoagent/abc123",
        )
        json_str = state.model_dump_json()
        restored = RunState.model_validate_json(json_str)
        assert restored.requirement == state.requirement
        assert restored.repo_url == state.repo_url
        assert restored.branch == state.branch
        assert restored.run_id == state.run_id

    def test_status_transitions(self) -> None:
        state = RunState()
        state.status = RunStatus.DONE
        assert state.status == RunStatus.DONE
        state.status = RunStatus.NEEDS_HUMAN
        assert state.status == RunStatus.NEEDS_HUMAN

    def test_retry_count_validation(self) -> None:
        with pytest.raises(ValidationError):
            RunState(retry_count=-1)

    def test_max_retries_validation(self) -> None:
        with pytest.raises(ValidationError):
            RunState(max_retries=0)

    def test_unique_run_ids(self) -> None:
        states = [RunState() for _ in range(100)]
        ids = {s.run_id for s in states}
        assert len(ids) == 100

    def test_full_state_json_round_trip(self) -> None:
        """Test a fully-populated RunState round-trips through JSON."""
        state = RunState(
            requirement="Add password reset",
            repo_url="https://github.com/test/repo",
            branch="repoagent/run123",
            repo_map="src/\n  app.py\n  models.py",
            plan=Plan(
                summary="Add reset endpoint",
                files_to_change=[PlannedFile(path="src/auth.py", reason="new endpoint")],
                steps=["Create route", "Add template"],
                risks=["Token expiry edge case"],
                test_strategy="Unit + integration tests",
            ),
            changes=ChangeSet(
                files=[FileChange(path="src/auth.py", action="create", diff="+def reset():")],
                summary="Added reset endpoint",
            ),
            test_reports=[
                TestReport(
                    command="pytest tests/",
                    passed=True,
                    exit_code=0,
                    failed_tests=[],
                    log_tail="10 passed",
                    duration_s=2.5,
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                findings=[
                    ReviewFinding(
                        severity="info",
                        file="src/auth.py",
                        line=42,
                        message="Consider adding docstring",
                    )
                ],
                summary="LGTM",
            ),
            pr=PRSummary(
                title="Add password reset",
                body_markdown="## Changes\n- Added reset endpoint",
                url="https://github.com/test/repo/pull/1",
                branch="repoagent/run123",
            ),
            status=RunStatus.DONE,
            errors=[],
            retry_count=1,
            finished_at=datetime.now(UTC),
        )
        json_str = state.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["status"] == "done"
        assert parsed["plan"]["summary"] == "Add reset endpoint"
        assert parsed["review"]["verdict"] == "approve"

        restored = RunState.model_validate_json(json_str)
        assert restored == state


class TestPlan:
    def test_creation(self) -> None:
        plan = Plan(
            summary="Test plan",
            files_to_change=[PlannedFile(path="a.py", reason="modify")],
            steps=["step1"],
            risks=["risk1"],
            test_strategy="pytest",
        )
        assert plan.summary == "Test plan"
        assert len(plan.files_to_change) == 1

    def test_json_round_trip(self) -> None:
        plan = Plan(summary="s", steps=["a", "b"])
        restored = Plan.model_validate_json(plan.model_dump_json())
        assert restored == plan

    def test_defaults(self) -> None:
        plan = Plan(summary="minimal")
        assert plan.files_to_change == []
        assert plan.steps == []
        assert plan.risks == []
        assert plan.test_strategy == ""


class TestChangeSet:
    def test_creation(self) -> None:
        cs = ChangeSet(
            files=[FileChange(path="x.py", action="create", diff="+hello")],
            summary="added x",
        )
        assert len(cs.files) == 1

    def test_json_round_trip(self) -> None:
        cs = ChangeSet(
            files=[FileChange(path="y.py", action="modify", diff="-old\n+new")],
            summary="changed y",
        )
        restored = ChangeSet.model_validate_json(cs.model_dump_json())
        assert restored == cs

    def test_frozen_file_change(self) -> None:
        fc = FileChange(path="z.py", action="delete")
        with pytest.raises(ValidationError):
            fc.path = "other.py"  # type: ignore[misc]


class TestTestReport:
    def test_passing(self) -> None:
        tr = TestReport(command="pytest", passed=True, exit_code=0, duration_s=1.2)
        assert tr.passed
        assert tr.failed_tests == []

    def test_failing(self) -> None:
        tr = TestReport(
            command="pytest",
            passed=False,
            exit_code=1,
            failed_tests=["test_foo", "test_bar"],
            log_tail="FAILED test_foo",
            duration_s=3.0,
        )
        assert not tr.passed
        assert len(tr.failed_tests) == 2

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TestReport(command="pytest", passed=True, exit_code=0, duration_s=-1.0)

    def test_json_round_trip(self) -> None:
        tr = TestReport(command="pytest -x", passed=False, exit_code=2, duration_s=5.0)
        restored = TestReport.model_validate_json(tr.model_dump_json())
        assert restored == tr


class TestReviewReport:
    def test_approve(self) -> None:
        rr = ReviewReport(verdict=ReviewVerdict.APPROVE, summary="LGTM")
        assert rr.verdict == ReviewVerdict.APPROVE

    def test_request_changes(self) -> None:
        rr = ReviewReport(
            verdict=ReviewVerdict.REQUEST_CHANGES,
            findings=[ReviewFinding(severity="critical", file="a.py", line=10, message="bug")],
        )
        assert len(rr.findings) == 1

    def test_frozen_finding(self) -> None:
        f = ReviewFinding(severity="warning", file="b.py", line=5, message="style")
        with pytest.raises(ValidationError):
            f.severity = "info"  # type: ignore[misc]

    def test_json_round_trip(self) -> None:
        rr = ReviewReport(
            verdict=ReviewVerdict.REQUEST_CHANGES,
            findings=[ReviewFinding(severity="info", file="c.py", line=0, message="nit")],
            summary="Minor issues",
        )
        restored = ReviewReport.model_validate_json(rr.model_dump_json())
        assert restored == rr


class TestPRSummary:
    def test_creation(self) -> None:
        pr = PRSummary(title="Fix bug", branch="repoagent/abc")
        assert pr.title == "Fix bug"
        assert pr.url == ""

    def test_json_round_trip(self) -> None:
        pr = PRSummary(
            title="Add feature",
            body_markdown="## PR\nDetails here",
            url="https://github.com/o/r/pull/42",
            branch="repoagent/xyz",
        )
        restored = PRSummary.model_validate_json(pr.model_dump_json())
        assert restored == pr


class TestEnums:
    def test_run_status_values(self) -> None:
        assert RunStatus.RUNNING.value == "running"
        assert RunStatus.NEEDS_HUMAN.value == "needs_human"
        assert RunStatus.BLOCKED.value == "blocked"
        assert RunStatus.DONE.value == "done"

    def test_review_verdict_values(self) -> None:
        assert ReviewVerdict.APPROVE.value == "approve"
        assert ReviewVerdict.REQUEST_CHANGES.value == "request_changes"

    def test_status_json_serialization(self) -> None:
        state = RunState(status=RunStatus.BLOCKED)
        data = json.loads(state.model_dump_json())
        assert data["status"] == "blocked"
