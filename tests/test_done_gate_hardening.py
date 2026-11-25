"""Regression tests for DONE Gate hardening, test/reviewer provenance, and PR promotion safety.

Covers:
1. Offline execution safety (no real PRs, no fabricated test reports).
2. Test execution provenance (rejects synthetic '12 passed in 0.08s', requires hash match).
3. Reviewer provenance (requires authoritative ADK review, rejects fallback).
4. DONE Gate fail-closed evaluation (authoritative evidence required, rejects missing acceptance).
5. PR promotion preflight (zero MCP calls for offline/fallback/failing runs).
6. Requirement acceptance verification (PR #6 word_count fixture:
   rejects handle_feature/assert True, accepts valid word_count with full edge-case coverage).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from repoagent.done_gate import evaluate_done_gate
from repoagent.flow import RepoAgentFlow
from repoagent.pr_agent import open_and_verify_pull_request
from repoagent.schemas import (
    ChangeSet,
    FileChange,
    ReviewFinding,
    ReviewReport,
    ReviewVerdict,
    RunState,
    RunStatus,
    TestReport,
)
from repoagent.workspace_utils import compute_workspace_hash

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_git_workspace(tmp_path: Path) -> Path:
    """Create a minimal mock git workspace simulating an isolated run directory."""
    import subprocess

    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(ws), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test Runner"], cwd=str(ws), capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(ws),
        capture_output=True,
        check=True,
    )

    # Initial baseline commit
    src_dir = ws / "src" / "notes_app"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "auth.py").write_text("class Authenticator:\n    pass\n", encoding="utf-8")

    tests_dir = ws / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_auth.py").write_text(
        "def test_dummy():\n    assert True\n", encoding="utf-8"
    )

    subprocess.run(["git", "add", "."], cwd=str(ws), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial baseline commit"],
        cwd=str(ws),
        capture_output=True,
        check=True,
    )
    return ws


# ---------------------------------------------------------------------------
# Category 1: Offline Execution Safety
# ---------------------------------------------------------------------------


class TestOfflineExecutionSafety:
    """Verifies offline/deterministic execution is isolated from live PR promotion."""

    def test_offline_run_cannot_authorize_real_pr_promotion(
        self, clean_git_workspace: Path
    ) -> None:
        """use_live_agents=False must never call live GitHub PR promotion."""
        ws = clean_git_workspace
        # Modify a file
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # offline change\n", encoding="utf-8"
        )

        state = RunState(
            run_id="run_offline_001",
            requirement="Add feature",
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest tests/ -v",
                    passed=True,
                    exit_code=0,
                    workspace_hash=compute_workspace_hash(ws),
                    raw_output="1 passed in 0.05s",
                    provenance="offline_fallback",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Fallback review approve for offline test execution",
                provenance="fallback",
            ),
        )

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="Cannot open PR:.*offline|fallback|provenance"):
                open_and_verify_pull_request(state, ws, is_live_execution=False)
            mock_mcp.assert_not_called()

    def test_offline_mode_does_not_fabricate_successful_test_report(
        self, clean_git_workspace: Path
    ) -> None:
        """In flow.py, offline mode must not inject '12 passed in 0.08s' without real execution."""
        ws = clean_git_workspace
        state = RunState(
            run_id="run_offline_002",
            requirement="Add word_count function",
            status=RunStatus.RUNNING,
            repo_url=str(ws),
        )

        flow = RepoAgentFlow(
            initial_state=state,
            workspace=ws,
            use_live_agents=False,
            create_pr=False,
        )

        # Execute test step
        flow.state.branch = "repoagent/run_offline_002"
        # Modify a file so modifications exist
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # changed\n", encoding="utf-8"
        )
        _ = flow.test()

        # The test step must have run real tests or recorded offline provenance
        assert len(flow.state.test_reports) > 0
        last_rep = flow.state.test_reports[-1]
        assert (
            last_rep.raw_output != "12 passed in 0.08s" or last_rep.provenance == "offline_fallback"
        )

    def test_offline_flow_does_not_activate_mock_bypass_and_cannot_produce_done(
        self, clean_git_workspace: Path
    ) -> None:
        """Offline flow without mock flags cannot pass the DONE Gate and ends in NEEDS_HUMAN."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # offline change\n", encoding="utf-8"
        )
        state = RunState(
            run_id="run_offline_fail_closed",
            requirement="Add feature",
            status=RunStatus.RUNNING,
            repo_url=str(ws),
        )

        flow = RepoAgentFlow(
            initial_state=state,
            workspace=ws,
            use_live_agents=False,
            create_pr=False,
        )
        flow.state.branch = "repoagent/run_offline_fail_closed"
        _ = flow.test()
        _ = flow.review()
        _ = flow.open_pr()

        # Must NOT be DONE, must fail closed to NEEDS_HUMAN
        assert flow.state.status == RunStatus.NEEDS_HUMAN
        assert any("Done gate conditions not satisfied" in err for err in flow.state.errors)

    def test_explicit_unit_test_mock_fixtures_still_work_with_mock_test_flag(
        self, clean_git_workspace: Path
    ) -> None:
        """Explicit mock fixtures work with is_mock_test=True for unit testing only."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # mock change\n", encoding="utf-8"
        )
        state = RunState(
            run_id="run_explicit_mock",
            requirement="Add feature",
            status=RunStatus.RUNNING,
            repo_url=str(ws),
        )

        flow = RepoAgentFlow(
            initial_state=state,
            workspace=ws,
            use_live_agents=False,
            create_pr=False,
            is_mock_test=True,
        )
        flow.state.branch = "repoagent/run_explicit_mock"
        _ = flow.test()
        _ = flow.review()
        _ = flow.open_pr()

        # Under explicit is_mock_test=True, unit testing status is allowed to be DONE
        assert flow.state.status == RunStatus.DONE
        # But PR promotion must still strictly fail
        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError):
                open_and_verify_pull_request(flow.state, ws, is_live_execution=False)
            mock_mcp.assert_not_called()


# ---------------------------------------------------------------------------
# Category 2: Test Execution Provenance
# ---------------------------------------------------------------------------


class TestExecutionProvenance:
    """Verifies that test execution evidence must have verifiable, authentic provenance."""

    def test_fabricated_12_passed_evidence_rejected_by_done_gate(
        self, clean_git_workspace: Path
    ) -> None:
        """The fabricated string '12 passed in 0.08s' with fallback provenance is rejected."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # changed\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_prov_001",
            requirement="Add word_count",
            status=RunStatus.RUNNING,
            test_reports=[
                TestReport(
                    command="pytest tests/ -v",
                    passed=True,
                    exit_code=0,
                    failed_tests=[],
                    log_tail="12 passed in 0.08s",
                    raw_output="12 passed in 0.08s",
                    workspace_hash=ws_hash,
                    provenance="synthetic_mock",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
        )

        passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
        assert passed is False
        assert (
            "provenance" in reason.lower()
            or "synthetic" in reason.lower()
            or "unauthoritative" in reason.lower()
        )

    def test_actual_failing_pytest_cannot_satisfy_done(self, clean_git_workspace: Path) -> None:
        """A test report with exit_code != 0 cannot satisfy DONE Gate."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # changed\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_prov_002",
            requirement="Add word_count",
            status=RunStatus.RUNNING,
            test_reports=[
                TestReport(
                    command="pytest tests/ -v",
                    passed=False,
                    exit_code=1,
                    failed_tests=["tests/test_auth.py::test_feature_stub"],
                    workspace_hash=ws_hash,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE, provenance="authoritative_adk_reviewer"
            ),
        )

        passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
        assert passed is False
        assert "failed" in reason.lower() or "exit code" in reason.lower()

    def test_mismatched_workspace_hash_fails_gate(self, clean_git_workspace: Path) -> None:
        """A test report with a workspace_hash differing from disk is rejected."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # changed\n", encoding="utf-8"
        )

        state = RunState(
            run_id="run_prov_003",
            requirement="Add feature",
            status=RunStatus.RUNNING,
            test_reports=[
                TestReport(
                    command="pytest tests/ -v",
                    passed=True,
                    exit_code=0,
                    workspace_hash="stale_hash_from_previous_attempt",
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE, provenance="authoritative_adk_reviewer"
            ),
        )

        passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
        assert passed is False
        assert "hash mismatch" in reason.lower()

    def test_missing_test_provenance_rejected_by_done_gate(self, clean_git_workspace: Path) -> None:
        """A TestReport with provenance=None is rejected by the strict allowlist."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # changed\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_prov_none",
            requirement="Add feature",
            status=RunStatus.RUNNING,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=ws_hash,
                    provenance=None,
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                provenance="authoritative_adk_reviewer",
                summary="LGTM",
            ),
        )

        passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
        assert passed is False
        assert "provenance" in (reason or "").lower()

    def test_unknown_arbitrary_test_provenance_rejected_by_done_gate(
        self, clean_git_workspace: Path
    ) -> None:
        """A TestReport with unknown provenance string is rejected by the strict allowlist."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # changed\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_prov_unknown",
            requirement="Add feature",
            status=RunStatus.RUNNING,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=ws_hash,
                    provenance="custom_unverified_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                provenance="authoritative_adk_reviewer",
                summary="LGTM",
            ),
        )

        passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
        assert passed is False
        assert "provenance" in (reason or "").lower()


# ---------------------------------------------------------------------------
# Category 3: Reviewer Provenance
# ---------------------------------------------------------------------------


class TestReviewerProvenance:
    """Verifies that code review must be authoritative and not synthetic/fallback."""

    def test_fallback_reviewer_approval_rejected_by_done_gate(
        self, clean_git_workspace: Path
    ) -> None:
        """Review reports containing fallback provenance fail the DONE Gate."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # changed\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_rev_001",
            requirement="Add feature",
            status=RunStatus.RUNNING,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=ws_hash,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                findings=[
                    ReviewFinding(
                        file="general", severity="info", message="Fallback test review approve"
                    )
                ],
                summary="Fallback review approve for offline test execution",
                provenance="fallback",
            ),
        )

        passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
        assert passed is False
        assert (
            "reviewer" in reason.lower()
            or "fallback" in reason.lower()
            or "provenance" in reason.lower()
        )

    def test_request_changes_review_rejected_by_done_gate(self, clean_git_workspace: Path) -> None:
        """A ReviewReport with REQUEST_CHANGES fails the DONE Gate."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # changed\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_rev_002",
            requirement="Add feature",
            status=RunStatus.RUNNING,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=ws_hash,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.REQUEST_CHANGES,
                findings=[
                    ReviewFinding(
                        file="auth.py", severity="critical", message="Unrelated file modified"
                    )
                ],
                summary="Changes requested",
                provenance="authoritative_adk_reviewer",
            ),
        )

        passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
        assert passed is False
        assert "verdict" in reason.lower() or "approve" in reason.lower()

    def test_missing_reviewer_provenance_rejected_by_done_gate(
        self, clean_git_workspace: Path
    ) -> None:
        """A ReviewReport with provenance=None is rejected by the strict allowlist."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # changed\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_rev_none",
            requirement="Add feature",
            status=RunStatus.RUNNING,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=ws_hash,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved without provenance",
                provenance=None,
            ),
        )

        passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
        assert passed is False
        assert "provenance" in (reason or "").lower()

    def test_unknown_arbitrary_reviewer_provenance_rejected_by_done_gate(
        self, clean_git_workspace: Path
    ) -> None:
        """A ReviewReport with an unknown provenance string is rejected."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # changed\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_rev_unknown",
            requirement="Add feature",
            status=RunStatus.RUNNING,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=ws_hash,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved with custom string",
                provenance="custom_llm_reviewer",
            ),
        )

        passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
        assert passed is False
        assert "provenance" in (reason or "").lower()

    def test_authoritative_reviewer_and_test_provenance_accepted(
        self, clean_git_workspace: Path
    ) -> None:
        """Exact authoritative provenance for reviewer and test is accepted."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # authoritative change\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_auth_valid",
            requirement="Add feature",
            status=RunStatus.RUNNING,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=ws_hash,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Authoritative review approve",
                provenance="authoritative_adk_reviewer",
            ),
        )

        passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
        assert passed is True
        assert reason is None


# ---------------------------------------------------------------------------
# Category 4 & 5: PR Promotion Preflight Safeguards
# ---------------------------------------------------------------------------


class TestPRPromotionSafeguards:
    """Verifies PR promotion fails closed with zero MCP writes on unauthoritative runs."""

    def test_fallback_reviewer_approval_makes_zero_github_mcp_calls(
        self, clean_git_workspace: Path
    ) -> None:
        """open_and_verify_pull_request makes zero MCP calls if review is fallback."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # mod\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_pr_001",
            requirement="Add feature",
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=ws_hash,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Fallback review approve for offline test execution",
                provenance="fallback",
            ),
        )

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="DONE gate|fallback|provenance"):
                open_and_verify_pull_request(state, ws)
            mock_mcp.assert_not_called()

    def test_failed_tests_make_zero_github_mcp_calls(self, clean_git_workspace: Path) -> None:
        """open_and_verify_pull_request makes zero MCP calls if test failed."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # mod\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_pr_002",
            requirement="Add feature",
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=False,
                    exit_code=1,
                    workspace_hash=ws_hash,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE, provenance="authoritative_adk_reviewer"
            ),
        )

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="DONE gate|failed"):
                open_and_verify_pull_request(state, ws)
            mock_mcp.assert_not_called()

    def test_missing_test_provenance_blocks_pr_promotion(self, clean_git_workspace: Path) -> None:
        """open_and_verify_pull_request blocks PR promotion if test provenance is missing."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text("# mod\n", encoding="utf-8")
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_pr_none_test",
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=ws_hash,
                    provenance=None,
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                provenance="authoritative_adk_reviewer",
            ),
        )

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="provenance.*not authoritative"):
                open_and_verify_pull_request(state, ws)
            mock_mcp.assert_not_called()

    def test_unknown_reviewer_provenance_blocks_pr_promotion(
        self, clean_git_workspace: Path
    ) -> None:
        """open_and_verify_pull_request blocks PR promotion if review provenance is unknown."""
        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text("# mod\n", encoding="utf-8")
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="run_pr_unknown_rev",
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=ws_hash,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                provenance="unrecognized_reviewer",
            ),
        )

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="provenance.*not authoritative"):
                open_and_verify_pull_request(state, ws)
            mock_mcp.assert_not_called()


# ---------------------------------------------------------------------------
# Category 6: Requirement Acceptance Verification (PR #6 word_count fixture)
# ---------------------------------------------------------------------------


class TestRequirementAcceptanceVerification:
    """Verifies candidates implement the actual requirement, rejecting stubs like PR #6."""

    PR6_REQUIREMENT = (
        "Add a word_count(text) utility function to the demo repository. "
        "It should return the number of whitespace-separated words in the input text. "
        "Handle empty strings and whitespace-only strings by returning 0. "
        "Add unit tests covering normal text, multiple spaces, leading and trailing spaces, "
        "empty input, and whitespace-only input. Do not modify unrelated files."
    )

    def test_reject_pr6_handle_feature_and_test_stub(self, clean_git_workspace: Path) -> None:
        """The PR #6 stub (handle_feature() / test_feature_stub() in auth) is rejected."""
        from repoagent.acceptance import verify_requirement_acceptance

        ws = clean_git_workspace
        # Apply the exact PR #6 changes
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    pass\n\ndef handle_feature():\n    return True\n",
            encoding="utf-8",
        )
        (ws / "tests" / "test_auth.py").write_text(
            "def test_dummy():\n    assert True\n\ndef test_feature_stub():\n    assert True\n",
            encoding="utf-8",
        )

        changeset = ChangeSet(
            files=[
                FileChange(
                    path="src/notes_app/auth.py",
                    action="modify",
                    diff="+def handle_feature():\n+    return True",
                ),
                FileChange(
                    path="tests/test_auth.py",
                    action="modify",
                    diff="+def test_feature_stub():\n+    assert True",
                ),
            ],
            summary="Initial change addressing Add a word_count(text) utility function",
        )

        accepted, reason = verify_requirement_acceptance(
            requirement=self.PR6_REQUIREMENT,
            workspace=ws,
            changeset=changeset,
        )

        assert accepted is False
        assert (
            "word_count" in reason.lower()
            or "unrelated" in reason.lower()
            or "stub" in reason.lower()
        )

    def test_reject_candidate_modifying_unrelated_files(self, clean_git_workspace: Path) -> None:
        """Modifying auth.py when word_count is requested is flagged as unrelated modification."""
        from repoagent.acceptance import verify_requirement_acceptance

        ws = clean_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class Authenticator:\n    # unrelated\n", encoding="utf-8"
        )

        changeset = ChangeSet(
            files=[FileChange(path="src/notes_app/auth.py", action="modify", diff="+ # unrelated")],
            summary="Changed auth",
        )

        accepted, reason = verify_requirement_acceptance(
            requirement=self.PR6_REQUIREMENT,
            workspace=ws,
            changeset=changeset,
        )

        assert accepted is False
        assert "unrelated" in reason.lower() or "word_count" in reason.lower()

    def test_accept_correct_word_count_implementation_with_behavioral_tests(
        self, clean_git_workspace: Path
    ) -> None:
        """A correct word_count(text) implementation with test edge cases passes acceptance."""
        from repoagent.acceptance import verify_requirement_acceptance

        ws = clean_git_workspace
        # Write real word_count implementation
        utils_dir = ws / "src" / "notes_app"
        (utils_dir / "utils.py").write_text(
            '"""Text utility functions."""\n\n'
            "def word_count(text: str) -> int:\n"
            '    """Return number of words in text. Empty or whitespace-only returns 0."""\n'
            "    if not text or not text.strip():\n"
            "        return 0\n"
            "    return len(text.strip().split())\n",
            encoding="utf-8",
        )

        # Write genuine behavioral unit tests covering all 5 required cases
        (ws / "tests" / "test_utils.py").write_text(
            "import pytest\n"
            "from notes_app.utils import word_count\n\n"
            "def test_word_count_normal_text():\n"
            '    assert word_count("hello world") == 2\n'
            '    assert word_count("the quick brown fox") == 4\n\n'
            "def test_word_count_multiple_spaces():\n"
            '    assert word_count("hello    world   again") == 3\n\n'
            "def test_word_count_leading_trailing_spaces():\n"
            '    assert word_count("   leading and trailing   ") == 3\n\n'
            "def test_word_count_empty_input():\n"
            '    assert word_count("") == 0\n\n'
            "def test_word_count_whitespace_only():\n"
            '    assert word_count("   \\t\\n\\r  ") == 0\n',
            encoding="utf-8",
        )

        changeset = ChangeSet(
            files=[
                FileChange(
                    path="src/notes_app/utils.py",
                    action="add",
                    diff="+def word_count(text: str) -> int:",
                ),
                FileChange(
                    path="tests/test_utils.py",
                    action="add",
                    diff="+def test_word_count_normal_text():",
                ),
            ],
            summary="Implement word_count utility with edge-case tests",
        )

        accepted, reason = verify_requirement_acceptance(
            requirement=self.PR6_REQUIREMENT,
            workspace=ws,
            changeset=changeset,
        )

        assert accepted is True
        assert reason is None or "verified" in reason.lower()
