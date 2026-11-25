"""Tests for the CrewAI Flow orchestrator and state transitions."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from repoagent.flow import RepoAgentFlow
from repoagent.run import main
from repoagent.schemas import ReviewReport, ReviewVerdict, RunState, RunStatus, TestReport


class TestRepoAgentFlow:
    """Test suite for RepoAgentFlow orchestration."""

    def test_flow_completes_end_to_end(self) -> None:
        """Verify the stub flow completes all steps from analyze to open_pr."""
        initial_state = RunState(
            requirement="Add password-reset functionality",
            repo_url="demo_repo",
        )
        flow = RepoAgentFlow(initial_state=initial_state, is_mock_test=True)
        result = flow.kickoff()

        assert result == "open_pr_completed"
        assert flow.state.status == RunStatus.DONE
        assert flow.state.finished_at is not None
        assert flow.state.retry_count == 0

        # Verify all state artifacts are populated
        assert flow.state.repo_map != ""
        assert flow.state.plan is not None
        assert "password-reset" in flow.state.plan.summary
        assert len(flow.state.plan.files_to_change) > 0

        assert flow.state.changes is not None
        assert len(flow.state.changes.files) > 0

        assert len(flow.state.test_reports) == 1
        assert flow.state.test_reports[0].passed is True

        assert flow.state.review is not None
        assert flow.state.review.verdict.value == "approve"

        assert flow.state.pr is not None
        assert flow.state.pr.title.startswith("feat:")
        assert flow.state.pr.branch.startswith("repoagent/")

    def test_retry_cap_path_ends_with_needs_human(self) -> None:
        """Verify repeated test failures trigger retries up to max_retries, then needs_human."""
        initial_state = RunState(
            requirement="Add password-reset with faulty tests",
            repo_url="demo_repo",
            max_retries=3,
        )
        flow = RepoAgentFlow(initial_state=initial_state, simulate_test_failures=5)
        result = flow.kickoff()

        assert result == "needs_human_completed"
        assert flow.state.status == RunStatus.NEEDS_HUMAN
        assert flow.state.retry_count == 3
        assert flow.state.pr is None
        assert flow.state.review is None
        assert len(flow.state.test_reports) == 4  # Initial + 3 retries
        for report in flow.state.test_reports:
            assert report.passed is False

        assert len(flow.state.errors) > 0
        assert any("Execution capped" in err for err in flow.state.errors)

    def test_state_sharing_between_steps(self) -> None:
        """Verify state written by one step is accessible and used by subsequent steps."""
        initial_state = RunState(
            requirement="Implement custom auth token",
            repo_url="demo_repo",
        )
        flow = RepoAgentFlow(initial_state=initial_state, is_mock_test=True)
        flow.kickoff()

        # Step 1: analyze set repo_map containing requirement and branch
        assert "Implement custom auth token" in flow.state.repo_map
        assert flow.state.branch.startswith("repoagent/")

        # Step 2: plan used requirement
        assert flow.state.plan is not None
        assert "Implement custom auth token" in flow.state.plan.summary

        # Step 3: code changes reflect requirement
        assert flow.state.changes is not None
        assert "Implement custom auth token" in flow.state.changes.summary

        # Step 4: test recorded in shared test_reports
        assert len(flow.state.test_reports) > 0

        # Step 5: review approved tested changes
        assert flow.state.review is not None
        assert flow.state.review.summary != ""

        # Step 6: PR integrates requirement, plan summary, and branch
        assert flow.state.pr is not None
        assert "Implement custom auth token" in flow.state.pr.title
        assert flow.state.pr.branch == flow.state.branch

    def test_branch_naming_invariant(self) -> None:
        """Verify work is performed strictly on repoagent/<run_id> and never on main."""
        initial_state = RunState(requirement="Security test")
        flow = RepoAgentFlow(initial_state=initial_state, is_mock_test=True)
        flow.kickoff()

        assert flow.state.branch != "main"
        assert flow.state.branch != "master"
        assert flow.state.branch.startswith("repoagent/")
        assert flow.state.run_id in flow.state.branch

    def test_cli_execution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify CLI entry point runs the flow and outputs valid JSON."""
        monkeypatch.setenv("REPOAGENT_MOCK_TEST", "1")
        stdout_capture = StringIO()
        test_args = ["--repo", "demo_repo", "--requirement", "Add password reset", "--stub"]

        with patch("sys.stdout", stdout_capture):
            exit_code = main(test_args)

        assert exit_code == 0
        output_str = stdout_capture.getvalue()
        # Find JSON payload in output
        json_start = output_str.find("{")
        assert json_start != -1
        json_payload = output_str[json_start:]
        data = json.loads(json_payload)

        assert data["requirement"] == "Add password reset"
        assert data["status"] == "done"
        assert data["branch"].startswith("repoagent/")

    def test_replay_mode_loads_reference_fixture(self, tmp_path: Path) -> None:
        """Verify replay mode loads the hand-written reference solution and sets replay flags."""
        initial_state = RunState(
            requirement="Add a password-reset functionality",
            is_replay=True,
        )
        flow = RepoAgentFlow(
            initial_state=initial_state,
            workspace=tmp_path,
            coding_fn=None,
            testing_fn=lambda s: TestReport(
                command="pytest",
                passed=True,
                exit_code=0,
                workspace_hash="mock_hash",
            ),
            review_fn=lambda p, d, t: ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved reference replay",
            ),
        )
        flow.kickoff()

        assert flow.state.is_replay is True
        assert flow.state.replay_label == "hand-written reference solution, not model output"
        assert flow.state.changes is not None
        assert len(flow.state.changes.files) == 3
        paths = [f.path for f in flow.state.changes.files]
        assert "src/notes_app/auth.py" in paths
        assert "src/notes_app/app.py" in paths
        assert "tests/test_auth.py" in paths
