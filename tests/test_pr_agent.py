"""Unit tests for PR Agent, report writer, and GitHub MCP integration.

All tests use mocked MCP responses and isolated temporary directories.
No real network calls to GitHub or modifications to ./runs/ occur.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from repoagent.flow import RepoAgentFlow
from repoagent.pr_agent import (
    build_deterministic_pr_content,
    get_workspace_disk_diff_files,
    open_and_verify_pull_request,
    promote_existing_run,
    write_run_reports,
)
from repoagent.schemas import (
    PRSummary,
    ReviewReport,
    ReviewVerdict,
    RunState,
    RunStatus,
    TestReport,
)
from repoagent.workspace_utils import compute_workspace_hash


@pytest.fixture
def dummy_git_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace initialized with a git baseline commit."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "src" / "notes_app").mkdir(parents=True)
    (ws / "tests").mkdir()

    auth_code = "class AuthService:\n    pass\n"
    (ws / "src" / "notes_app" / "auth.py").write_text(auth_code, encoding="utf-8")
    test_code = "def test_dummy(): pass\n"
    (ws / "tests" / "test_auth.py").write_text(test_code, encoding="utf-8")

    # Git init and baseline commit
    subprocess.run(["git", "init"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Tester"], cwd=str(ws), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "tester@example.com"],
        cwd=str(ws),
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"], cwd=str(ws), check=True, capture_output=True
    )
    return ws


class TestPRAgentUnit:
    """Unit tests for PR Agent, Preflight, Verification, and Reporting."""

    def test_replay_label_and_deterministic_content(self) -> None:
        """Verify PR title and body contain required deterministic sections and REPLAY notice."""
        state = RunState(
            run_id="test1234",
            requirement="Add password reset",
            is_replay=True,
            retry_count=1,
            max_retries=3,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash="abc123def456",
                    log_tail="tests/test_auth.py::TestAuth::test_reset_password PASSED [100%]",
                    duration_s=0.45,
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="LGTM, clean implementation",
            ),
        )

        title, body = build_deterministic_pr_content(state, ["src/notes_app/auth.py"])

        assert title == "[REPLAY] feat: Add password reset"
        assert "Hand-written reference solution, not model output." in body
        assert "## Requirement\nAdd password reset" in body
        assert "## Files Changed\n- `src/notes_app/auth.py`" in body
        assert "| `pytest` | PASS | 0 | `abc123def456...` | 0.45s |" in body
        assert "- `tests/test_auth.py::TestAuth::test_reset_password`" in body
        assert "**Verdict**: `approve`" in body
        assert "- **Correction Iterations**: 1 / 3" in body
        assert "Created by RepoAgent. A human must review before merging." in body

    def test_token_never_appears_in_logs_or_body(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confirm GITHUB_TOKEN does not leak into PR body, report.md, or report.json."""
        secret_token = "github_pat_SUPER_SECRET_TOKEN_DO_NOT_LEAK_9999"
        monkeypatch.setenv("GITHUB_TOKEN", secret_token)

        state = RunState(
            run_id="sec_test",
            requirement="Secret test requirement",
            pr=PRSummary(
                title="feat: test",
                body_markdown="Normal body",
                url="https://github.com/owner/repo/pull/1",
                branch="repoagent/sec_test",
            ),
        )

        _, body = build_deterministic_pr_content(state, ["sample.py"])
        assert secret_token not in body

        run_dir = tmp_path / "run_sec"
        write_run_reports(run_dir, state)

        report_json = (run_dir / "report.json").read_text(encoding="utf-8")
        report_md = (run_dir / "report.md").read_text(encoding="utf-8")

        assert secret_token not in report_json
        assert secret_token not in report_md

    def test_files_outside_diff_never_pushed(self, dummy_git_workspace: Path) -> None:
        """Verify forbidden files like .env, runs/, and .pyc are never in disk diff files."""
        ws = dummy_git_workspace
        # Create modified file
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # updated\n", encoding="utf-8"
        )

        # Create forbidden files
        (ws / ".env").write_text("SECRET=123", encoding="utf-8")
        runs_dir = ws / "runs"
        runs_dir.mkdir()
        (runs_dir / "artifact.log").write_text("log data", encoding="utf-8")
        (ws / "dummy.pyc").write_text("bytecode", encoding="utf-8")

        changed = get_workspace_disk_diff_files(ws)

        assert "src/notes_app/auth.py" in changed
        assert ".env" not in changed
        assert "runs/artifact.log" not in changed
        assert "dummy.pyc" not in changed
        assert all(
            not k.startswith(".env") and not k.startswith("runs") and not k.endswith(".pyc")
            for k in changed
        )

    def test_baseline_mismatch_prevents_branch_creation(
        self, dummy_git_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that a remote baseline mismatch stops execution before any branch is created."""
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # modified\n", encoding="utf-8"
        )

        ws_hash = compute_workspace_hash(ws)
        state = RunState(
            run_id="mismatch_run",
            requirement="Req",
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
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
        )

        # Remote content differs from workspace baseline ("class AuthService:\n    pass\n")
        mock_remote_file = json.dumps(
            {
                "name": "auth.py",
                "path": "src/notes_app/auth.py",
                "content": "class AuthService:\n    # REMOTE DRIFTED DIFFERENT CODE\n",
                "encoding": "utf-8",
            }
        )

        created_branches: list[str] = []

        async def fake_call_tool(
            params: Any, tool_name: str, arguments: dict[str, Any] | None = None
        ) -> str:
            if tool_name == "get_file_contents":
                return mock_remote_file
            if tool_name == "create_branch":
                created_branches.append(arguments.get("branch", ""))
                return json.dumps({"ref": f"refs/heads/{arguments.get('branch')}"})
            return "{}"

        monkeypatch.setattr("repoagent.pr_agent.call_tool_direct", fake_call_tool)
        monkeypatch.setenv("GITHUB_TOKEN", "mock_token")

        with pytest.raises(ValueError, match="Baseline preflight failed"):
            open_and_verify_pull_request(state, ws)

        # Assert no branch was created
        assert len(created_branches) == 0

    def test_pr_creation_and_verification_success(
        self, dummy_git_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify successful PR creation, atomic file push, and post-creation verification."""
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # feature added\n", encoding="utf-8"
        )

        ws_hash = compute_workspace_hash(ws)
        state = RunState(
            run_id="success_run",
            requirement="Add password reset",
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
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
        )

        # Matching baseline remote content
        mock_remote_baseline = json.dumps(
            {
                "name": "auth.py",
                "path": "src/notes_app/auth.py",
                "content": "class AuthService:\n    pass\n",
            }
        )

        called_tools: list[str] = []

        async def fake_call_tool(
            params: Any, tool_name: str, arguments: dict[str, Any] | None = None
        ) -> str:
            called_tools.append(tool_name)
            args = arguments or {}
            if tool_name == "get_file_contents":
                return mock_remote_baseline
            if tool_name == "create_branch":
                return json.dumps({"ref": f"refs/heads/{args.get('branch')}"})
            if tool_name == "push_files":
                return json.dumps({"commit": "sha_push_123"})
            if tool_name == "create_pull_request":
                return json.dumps(
                    {
                        "number": 101,
                        "html_url": "https://github.com/Kushagra-Pachauri/demo_repo/pull/101",
                        "state": "open",
                    }
                )
            if tool_name == "get_pull_request":
                return json.dumps(
                    {
                        "state": "open",
                        "base": {"ref": "main"},
                        "head": {"ref": args.get("head", f"repoagent/{state.run_id}")},
                        "html_url": "https://github.com/Kushagra-Pachauri/demo_repo/pull/101",
                    }
                )
            if tool_name == "get_pull_request_files":
                return json.dumps(
                    [
                        {
                            "filename": "src/notes_app/auth.py",
                            "status": "modified",
                            "additions": 5,
                            "deletions": 0,
                            "changes": 5,
                        }
                    ]
                )
            return "{}"

        monkeypatch.setattr("repoagent.pr_agent.call_tool_direct", fake_call_tool)
        monkeypatch.setenv("GITHUB_TOKEN", "mock_token")

        pr_summary = open_and_verify_pull_request(state, ws)

        assert isinstance(pr_summary, PRSummary)
        assert pr_summary.pull_number == 101
        assert pr_summary.url == "https://github.com/Kushagra-Pachauri/demo_repo/pull/101"
        assert pr_summary.branch == f"repoagent/{state.run_id}"

        # Confirm all 5 expected tool calls executed in order
        assert called_tools == [
            "get_file_contents",
            "create_branch",
            "push_files",
            "create_pull_request",
            "get_pull_request",
            "get_pull_request_files",
        ]

    def test_verification_mismatch_fails(
        self, dummy_git_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that a mismatch between PR files and disk diff files raises RuntimeError."""
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # change\n", encoding="utf-8"
        )

        ws_hash = compute_workspace_hash(ws)
        state = RunState(
            run_id="verif_fail",
            requirement="Req",
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
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
        )

        async def fake_call_tool(
            params: Any, tool_name: str, arguments: dict[str, Any] | None = None
        ) -> str:
            if tool_name == "get_file_contents":
                return json.dumps({"content": "class AuthService:\n    pass\n"})
            if tool_name == "create_branch":
                return "{}"
            if tool_name == "push_files":
                return "{}"
            if tool_name == "create_pull_request":
                return json.dumps(
                    {"number": 202, "html_url": "https://github.com/org/repo/pull/202"}
                )
            if tool_name == "get_pull_request":
                return json.dumps(
                    {
                        "state": "open",
                        "base": {"ref": "main"},
                        "head": {"ref": f"repoagent/{state.run_id}"},
                    }
                )
            if tool_name == "get_pull_request_files":
                # Unexpected file returned
                return json.dumps([{"filename": "unexpected_other_file.py", "additions": 1}])
            return "{}"

        monkeypatch.setattr("repoagent.pr_agent.call_tool_direct", fake_call_tool)
        monkeypatch.setenv("GITHUB_TOKEN", "mock_token")

        with pytest.raises(RuntimeError, match="PR verification failed"):
            open_and_verify_pull_request(state, ws)

    def test_gate_not_satisfied_means_no_pr(self, dummy_git_workspace: Path) -> None:
        """Verify that when done gate is not satisfied, flow terminates at needs_human."""
        ws = dummy_git_workspace
        # Leave workspace unchanged (no modifications)
        state = RunState(
            run_id="gate_fail",
            requirement="Req",
            test_reports=[TestReport(command="pytest", passed=False, exit_code=1)],
        )

        with patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open_pr:
            flow = RepoAgentFlow(
                initial_state=state,
                workspace=ws,
                create_pr=True,
            )
            flow.open_pr()

            assert flow.state.status == RunStatus.NEEDS_HUMAN
            assert flow.state.pr is None
            mock_open_pr.assert_not_called()

    def test_pr_disabled_by_default(
        self, dummy_git_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that when create_pr is False and REPOAGENT_LIVE_PR is unset, no PR is opened."""
        monkeypatch.delenv("REPOAGENT_LIVE_PR", raising=False)
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # modified\n", encoding="utf-8"
        )

        ws_hash = compute_workspace_hash(ws)
        state = RunState(
            run_id="offline_pass",
            requirement="Req",
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
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
        )

        with patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open_pr:
            flow = RepoAgentFlow(
                initial_state=state,
                workspace=ws,
                create_pr=False,
                is_mock_test=True,
            )
            flow.open_pr()

            assert flow.state.status == RunStatus.DONE
            assert flow.state.pr is None or "offline-test" in flow.state.pr.url
            mock_open_pr.assert_not_called()

    def test_report_json_and_md_written(self, tmp_path: Path) -> None:
        """Verify report.json and report.md are written at the end of every run."""
        state = RunState(
            run_id="report_test",
            requirement="Add password reset",
            is_replay=True,
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash="hash999",
                    duration_s=0.35,
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Review passed",
            ),
            pr=PRSummary(
                title="[REPLAY] feat: Add password reset",
                url="https://github.com/Kushagra-Pachauri/demo_repo/pull/15",
                branch="repoagent/report_test",
                pull_number=15,
            ),
        )

        run_dir = tmp_path / "run_reports"
        write_run_reports(run_dir, state)

        assert (run_dir / "report.json").is_file()
        assert (run_dir / "report.md").is_file()

        json_data = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        assert json_data["run_id"] == "report_test"
        assert json_data["status"] == "done"
        assert json_data["pr"]["pull_number"] == 15

        md_text = (run_dir / "report.md").read_text(encoding="utf-8")
        assert "# RepoAgent Run Report: `report_test`" in md_text
        assert "- **Final Status**: `DONE`" in md_text
        assert "## Test Reports" in md_text
        assert "## Pull Request" in md_text
        assert "https://github.com/Kushagra-Pachauri/demo_repo/pull/15" in md_text

    def test_replay_mode_rejects_arbitrary_custom_requirement(self, tmp_path: Path) -> None:
        """Verify that passing --replay with an arbitrary custom requirement raises ValueError."""
        state = RunState(
            run_id="custom_replay_fail",
            requirement="Add email format validation in AuthService",
            is_replay=True,
        )
        with pytest.raises(ValueError, match="REPLAY mode rejected"):
            RepoAgentFlow(initial_state=state, workspace=tmp_path)

    def test_promote_run_state_inconsistency_rejected(self, tmp_path: Path) -> None:
        """Verify that conflicting report.json and run_state.json files fail the integrity check."""
        run_dir = tmp_path / "inconsistent_run"
        run_dir.mkdir(parents=True, exist_ok=True)
        rep_state = RunState(
            run_id="inconsistent_run",
            requirement="Req A",
            status=RunStatus.DONE,
        )
        chk_state = RunState(
            run_id="inconsistent_run",
            requirement="Req B",
            status=RunStatus.NEEDS_HUMAN,
        )
        (run_dir / "report.json").write_text(rep_state.model_dump_json(), encoding="utf-8")
        (run_dir / "run_state.json").write_text(chk_state.model_dump_json(), encoding="utf-8")

        with pytest.raises(ValueError, match="State integrity check failed"):
            promote_existing_run("inconsistent_run", runs_dir=tmp_path)

    def test_promote_run_refuses_path_traversal(self, tmp_path: Path) -> None:
        """Verify that path traversal attempts in run_id are rejected before disk access."""
        with pytest.raises(ValueError, match="Invalid run_id"):
            promote_existing_run("../../evil_run", runs_dir=tmp_path)
        with pytest.raises(ValueError, match="Invalid run_id"):
            promote_existing_run("run/sub", runs_dir=tmp_path)

    def test_promote_run_refuses_needs_human(self, tmp_path: Path) -> None:
        """Verify that a run ending in needs_human cannot be promoted to a PR."""
        run_dir = tmp_path / "failed_run"
        run_dir.mkdir(parents=True, exist_ok=True)
        state = RunState(
            run_id="failed_run",
            requirement="Req",
            status=RunStatus.NEEDS_HUMAN,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=False,
                    exit_code=1,
                    failed_tests=["test_foo"],
                )
            ],
        )
        (run_dir / "report.json").write_text(state.model_dump_json(), encoding="utf-8")
        (run_dir / "run_state.json").write_text(state.model_dump_json(), encoding="utf-8")

        with pytest.raises(ValueError, match="status is 'needs_human'"):
            promote_existing_run("failed_run", runs_dir=tmp_path)

    def test_promote_run_success_for_live_feature(self, tmp_path: Path) -> None:
        """Verify that promoting a successful live run uses exact live requirement and workspace."""
        from repoagent.workspace_utils import setup_workspace

        run_id = "live_search_run"
        run_dir = tmp_path / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        ws = run_dir / "workspace"
        setup_workspace(Path("demo_repo"), ws)

        # Apply live feature modifications to workspace
        notes_file = ws / "src" / "notes_app" / "notes.py"
        orig_content = notes_file.read_text(encoding="utf-8")
        notes_file.write_text(orig_content + "\n# Live search feature added\n", encoding="utf-8")

        state = RunState(
            run_id=run_id,
            requirement="Add note search functionality",
            branch=f"repoagent/{run_id}",
            status=RunStatus.DONE,
            is_replay=False,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=compute_workspace_hash(ws),
                    duration_s=0.25,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved live search changes",
                provenance="authoritative_adk_reviewer",
            ),
        )

        (run_dir / "report.json").write_text(state.model_dump_json(), encoding="utf-8")
        (run_dir / "run_state.json").write_text(state.model_dump_json(), encoding="utf-8")

        promoted = promote_existing_run(run_id, runs_dir=tmp_path, live_pr=False)
        assert promoted.status == RunStatus.DONE
        assert promoted.requirement == "Add note search functionality"
        assert promoted.is_replay is False
        assert promoted.pr is not None
        assert promoted.pr.title == "feat: Add note search functionality"
        assert "[REPLAY]" not in promoted.pr.title
        assert promoted.pr.branch == f"repoagent/{run_id}"

    def test_promote_run_duplicate_pr_prevention(self, tmp_path: Path) -> None:
        """Verify that promoting a run that already has an active PR does not duplicate it."""
        from repoagent.workspace_utils import setup_workspace

        run_id = "idempotent_run"
        run_dir = tmp_path / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        ws = run_dir / "workspace"
        setup_workspace(Path("demo_repo"), ws)

        # Apply modification
        auth_file = ws / "src" / "notes_app" / "auth.py"
        auth_text = auth_file.read_text(encoding="utf-8") + "\n# Modified\n"
        auth_file.write_text(auth_text, encoding="utf-8")

        state = RunState(
            run_id=run_id,
            requirement="Add password-reset functionality",
            branch=f"repoagent/{run_id}",
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=compute_workspace_hash(ws),
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
            pr=PRSummary(
                title="feat: Add password-reset functionality",
                url="https://github.com/Kushagra-Pachauri/demo_repo/pull/42",
                branch=f"repoagent/{run_id}",
                pull_number=42,
            ),
        )

        (run_dir / "report.json").write_text(state.model_dump_json(), encoding="utf-8")
        (run_dir / "run_state.json").write_text(state.model_dump_json(), encoding="utf-8")

        with patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open:
            result = promote_existing_run(run_id, runs_dir=tmp_path, live_pr=True)
            assert result.pr is not None
            assert result.pr.pull_number == 42
            assert result.pr.url == "https://github.com/Kushagra-Pachauri/demo_repo/pull/42"
            mock_open.assert_not_called()

    def test_needs_human_run_makes_zero_pr_api_calls(self, tmp_path: Path) -> None:
        """Assert zero PR API calls are made when flow ends in needs_human."""
        from repoagent.workspace_utils import setup_workspace

        ws = tmp_path / "workspace"
        setup_workspace(Path("demo_repo"), ws)

        state = RunState(
            requirement="Add notes search feature",
            status=RunStatus.NEEDS_HUMAN,
        )
        flow = RepoAgentFlow(initial_state=state, workspace=ws, create_pr=True)
        with (
            patch("repoagent.pr_agent.call_tool_direct") as mock_mcp,
            patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open_pr,
        ):
            result = flow.open_pr()
            assert result == "needs_human"
            mock_mcp.assert_not_called()
            mock_open_pr.assert_not_called()

    def test_failed_run_makes_zero_pr_api_calls(self, tmp_path: Path) -> None:
        """Assert zero PR API calls on open_and_verify_pull_request for failed run."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        state = RunState(
            requirement="Add notes tags",
            status=RunStatus.BLOCKED,
        )
        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="Cannot open PR"):
                open_and_verify_pull_request(state, ws)
            mock_mcp.assert_not_called()

    def test_test_failure_with_create_pr_makes_zero_pr_api_calls(self, tmp_path: Path) -> None:
        """Assert that test failures make zero PR API calls even when create_pr is True."""
        from repoagent.workspace_utils import setup_workspace

        ws = tmp_path / "workspace"
        setup_workspace(Path("demo_repo"), ws)

        notes_file = ws / "src" / "notes_app" / "notes.py"
        notes_file.write_text(
            notes_file.read_text(encoding="utf-8") + "\n# note mod\n", encoding="utf-8"
        )

        state = RunState(
            requirement="Add note export",
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=False,
                    exit_code=1,
                    failed_tests=["test_export"],
                    workspace_hash="mock_hash_fail",
                )
            ],
            review=ReviewReport(verdict=ReviewVerdict.APPROVE, summary="ok"),
        )
        flow = RepoAgentFlow(initial_state=state, workspace=ws, create_pr=True)
        with (
            patch("repoagent.pr_agent.call_tool_direct") as mock_mcp,
            patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open_pr,
        ):
            flow.open_pr()
            assert flow.state.status == RunStatus.NEEDS_HUMAN
            mock_mcp.assert_not_called()
            mock_open_pr.assert_not_called()

    def test_reviewer_rejection_makes_zero_pr_api_calls(self, tmp_path: Path) -> None:
        """Assert reviewer rejection (REQUEST_CHANGES) results in zero PR API calls."""
        from repoagent.workspace_utils import setup_workspace

        ws = tmp_path / "workspace"
        setup_workspace(Path("demo_repo"), ws)
        notes_file = ws / "src" / "notes_app" / "notes.py"
        notes_file.write_text(
            notes_file.read_text(encoding="utf-8") + "\n# note mod\n", encoding="utf-8"
        )

        state = RunState(
            requirement="Add note search",
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash="mock_hash_pass",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.REQUEST_CHANGES,
                summary="Security flaw detected",
            ),
        )
        flow = RepoAgentFlow(initial_state=state, workspace=ws, create_pr=True)
        with (
            patch("repoagent.pr_agent.call_tool_direct") as mock_mcp,
            patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open_pr,
        ):
            flow.open_pr()
            assert flow.state.status == RunStatus.NEEDS_HUMAN
            mock_mcp.assert_not_called()
            mock_open_pr.assert_not_called()

    def test_exception_during_flow_makes_zero_pr_api_calls(self, tmp_path: Path) -> None:
        """Assert exception in coding/testing/review routes safely with zero PR calls."""
        from repoagent.workspace_utils import setup_workspace

        ws = tmp_path / "workspace"
        setup_workspace(Path("demo_repo"), ws)

        def failing_coder(st: RunState):
            raise RuntimeError("Unexpected LLM crash during coding")

        state = RunState(
            requirement="Add custom feature",
            max_retries=1,
        )
        flow = RepoAgentFlow(
            initial_state=state,
            workspace=ws,
            coding_fn=failing_coder,
            create_pr=True,
        )
        with (
            patch("repoagent.pr_agent.call_tool_direct") as mock_mcp,
            patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open_pr,
        ):
            flow.code()
            assert flow.state.status in (RunStatus.NEEDS_HUMAN, RunStatus.RUNNING)
            mock_mcp.assert_not_called()
            mock_open_pr.assert_not_called()

    def test_missing_or_malformed_artifacts_makes_zero_pr_api_calls(self, tmp_path: Path) -> None:
        """Assert promoting run with missing/corrupted artifacts makes zero PR API calls."""
        run_id = "malformed_run"
        run_dir = tmp_path / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.json").write_text("{invalid json", encoding="utf-8")
        (run_dir / "run_state.json").write_text("{invalid json", encoding="utf-8")

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="Corrupted or invalid"):
                promote_existing_run(run_id, runs_dir=tmp_path, live_pr=True)
            mock_mcp.assert_not_called()

    def test_mismatched_state_and_report_makes_zero_pr_api_calls(self, tmp_path: Path) -> None:
        """Assert mismatched report.json and run_state.json abort promotion with 0 PR calls."""
        run_id = "mismatched_run"
        run_dir = tmp_path / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        state1 = RunState(run_id=run_id, requirement="Req A", status=RunStatus.DONE)
        state2 = RunState(run_id=run_id, requirement="Req B", status=RunStatus.DONE)

        (run_dir / "report.json").write_text(state1.model_dump_json(), encoding="utf-8")
        (run_dir / "run_state.json").write_text(state2.model_dump_json(), encoding="utf-8")

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="conflicting state files"):
                promote_existing_run(run_id, runs_dir=tmp_path, live_pr=True)
            mock_mcp.assert_not_called()

    def test_custom_requirement_cannot_silently_use_password_reset_replay(self) -> None:
        """Assert that an arbitrary custom requirement cannot run in REPLAY mode."""
        state = RunState(
            requirement="Add user profile image upload",
            is_replay=True,
        )
        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="REPLAY mode rejected"):
                RepoAgentFlow(initial_state=state)
            mock_mcp.assert_not_called()

    def test_distinct_custom_requirements_receive_distinct_identities_and_changes(
        self, tmp_path: Path
    ) -> None:
        """Assert distinct requirements receive distinct run IDs, workspaces, and changes."""
        from repoagent.workspace_utils import setup_workspace

        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"
        setup_workspace(Path("demo_repo"), ws1)
        setup_workspace(Path("demo_repo"), ws2)

        state1 = RunState(requirement="Add notes search by keyword")
        state2 = RunState(requirement="Add note archiving functionality")

        assert state1.run_id != state2.run_id

        flow1 = RepoAgentFlow(initial_state=state1, workspace=ws1)
        flow2 = RepoAgentFlow(initial_state=state2, workspace=ws2)

        flow1.analyze()
        flow2.analyze()

        assert flow1.state.branch != flow2.state.branch
        assert flow1.state.requirement == "Add notes search by keyword"
        assert flow2.state.requirement == "Add note archiving functionality"

        flow1.plan()
        flow2.plan()

        assert "search" in flow1.state.plan.summary
        assert "archiving" in flow2.state.plan.summary

        flow1.code()
        flow2.code()

        assert "search" in flow1.state.changes.summary
        assert "archiving" in flow2.state.changes.summary

    def test_successful_eligible_run_promoted_only_when_create_pr_enabled(
        self, tmp_path: Path
    ) -> None:
        """Assert that an eligible DONE run only makes GitHub calls when live_pr is True."""
        from repoagent.workspace_utils import setup_workspace

        run_id = "eligible_run_1"
        run_dir = tmp_path / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        ws = run_dir / "workspace"
        setup_workspace(Path("demo_repo"), ws)

        # Modify workspace
        auth_file = ws / "src" / "notes_app" / "auth.py"
        auth_file.write_text(
            auth_file.read_text(encoding="utf-8") + "\n# new valid code\n", encoding="utf-8"
        )

        state = RunState(
            run_id=run_id,
            requirement="Add password-reset functionality",
            branch=f"repoagent/{run_id}",
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=compute_workspace_hash(ws),
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
        )

        (run_dir / "report.json").write_text(state.model_dump_json(), encoding="utf-8")
        (run_dir / "run_state.json").write_text(state.model_dump_json(), encoding="utf-8")

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            # When live_pr=False: NO GitHub calls made
            res_offline = promote_existing_run(run_id, runs_dir=tmp_path, live_pr=False)
            assert res_offline.status == RunStatus.DONE
            assert res_offline.pr is not None
            assert "offline-test" in res_offline.pr.url
            mock_mcp.assert_not_called()

        with patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open:
            mock_open.return_value = PRSummary(
                title="feat: Add password-reset functionality",
                url="https://github.com/Kushagra-Pachauri/demo_repo/pull/99",
                branch=f"repoagent/{run_id}",
                pull_number=99,
            )
            res_live = promote_existing_run(run_id, runs_dir=tmp_path, live_pr=True)
            assert res_live.pr.pull_number == 99
            mock_open.assert_called_once()

    def test_repeating_promotion_is_idempotent_and_creates_no_duplicate_prs(
        self, tmp_path: Path
    ) -> None:
        """Assert repeating promotion on an already promoted run does not create duplicate PRs."""
        from repoagent.workspace_utils import setup_workspace

        run_id = "already_promoted_run"
        run_dir = tmp_path / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        ws = run_dir / "workspace"
        setup_workspace(Path("demo_repo"), ws)

        auth_file = ws / "src" / "notes_app" / "auth.py"
        auth_file.write_text(auth_file.read_text(encoding="utf-8") + "\n# mod\n", encoding="utf-8")

        state = RunState(
            run_id=run_id,
            requirement="Add password-reset functionality",
            branch=f"repoagent/{run_id}",
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=compute_workspace_hash(ws),
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
            pr=PRSummary(
                title="feat: Add password-reset functionality",
                url="https://github.com/Kushagra-Pachauri/demo_repo/pull/77",
                branch=f"repoagent/{run_id}",
                pull_number=77,
            ),
        )

        (run_dir / "report.json").write_text(state.model_dump_json(), encoding="utf-8")
        (run_dir / "run_state.json").write_text(state.model_dump_json(), encoding="utf-8")

        with (
            patch("repoagent.pr_agent.call_tool_direct") as mock_mcp,
            patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open,
        ):
            res1 = promote_existing_run(run_id, runs_dir=tmp_path, live_pr=True)
            res2 = promote_existing_run(run_id, runs_dir=tmp_path, live_pr=True)
            assert res1.pr.pull_number == 77
            assert res2.pr.pull_number == 77
            mock_mcp.assert_not_called()
            mock_open.assert_not_called()

    def test_failure_path_in_cli_makes_zero_pr_api_calls(self, tmp_path: Path) -> None:
        """Assert CLI failure paths make zero GitHub PR API calls."""
        from repoagent.run import main

        with (
            patch("repoagent.pr_agent.call_tool_direct") as mock_mcp,
            patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open,
        ):
            # 1. Invalid from_run
            ret1 = main(["--from-run", "non_existent_run_123", "--create-pr"])
            assert ret1 == 1

            # 2. Replay with invalid custom requirement
            with pytest.raises(SystemExit):
                main(["--requirement", "Custom unknown requirement", "--replay", "--create-pr"])

            mock_mcp.assert_not_called()
            mock_open.assert_not_called()

    def test_regression_workspace_changes_after_testing_rejected(
        self, dummy_git_workspace: Path
    ) -> None:
        """Regression 1: Workspace changes after testing cause hash mismatch rejection."""
        ws = dummy_git_workspace
        target_file = ws / "src" / "notes_app" / "auth.py"
        target_file.write_text("class AuthService:\n    # at testing time\n", encoding="utf-8")

        test_time_hash = compute_workspace_hash(ws)
        state = RunState(
            run_id="stale_evidence_run",
            requirement="Req",
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=test_time_hash,
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
        )

        # Workspace is modified AFTER testing
        target_file.write_text("class AuthService:\n    # modified after tests\n", encoding="utf-8")

        # evaluate_done_gate must reject
        from repoagent.done_gate import evaluate_done_gate

        passed, reason = evaluate_done_gate(state, ws)
        assert passed is False
        assert "Workspace hash mismatch" in (reason or "")

        # open_and_verify_pull_request must raise ValueError with zero MCP calls
        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="DONE gate conditions not satisfied"):
                open_and_verify_pull_request(state, ws)
            mock_mcp.assert_not_called()

    def test_regression_arbitrary_non_empty_hashes_rejected(
        self, dummy_git_workspace: Path
    ) -> None:
        """Regression 2: Arbitrary non-empty hashes are rejected."""
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # valid change\n", encoding="utf-8"
        )

        state = RunState(
            run_id="arbitrary_hash_run",
            requirement="Req",
            status=RunStatus.DONE,
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash="arbitrary_non_empty_fake_hash_value_12345",
                    provenance="authoritative_docker_sandbox",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
        )

        from repoagent.done_gate import evaluate_done_gate

        passed, reason = evaluate_done_gate(state, ws)
        assert passed is False
        assert "Workspace hash mismatch" in (reason or "")

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="DONE gate conditions not satisfied"):
                open_and_verify_pull_request(state, ws)
            mock_mcp.assert_not_called()

    def test_regression_running_with_empty_reports_zero_pr_calls(
        self, dummy_git_workspace: Path
    ) -> None:
        """Regression 3: RUNNING + empty reports causes zero GitHub MCP write calls."""
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # in progress\n", encoding="utf-8"
        )
        state = RunState(
            run_id="running_empty_run",
            requirement="Req",
            status=RunStatus.RUNNING,
            test_reports=[],
        )

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="Run status is 'running'"):
                open_and_verify_pull_request(state, ws)
            mock_mcp.assert_not_called()

    def test_regression_running_with_populated_reports_zero_pr_calls(
        self, dummy_git_workspace: Path
    ) -> None:
        """Regression 4: RUNNING + populated reports also causes zero GitHub MCP write calls."""
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # change\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)
        state = RunState(
            run_id="running_populated_run",
            requirement="Req",
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
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
        )

        with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
            with pytest.raises(ValueError, match="Run status is 'running'"):
                open_and_verify_pull_request(state, ws)
            mock_mcp.assert_not_called()

    def test_regression_needs_human_and_blocked_zero_pr_calls(
        self, dummy_git_workspace: Path
    ) -> None:
        """Regression 5: NEEDS_HUMAN and BLOCKED states cause zero GitHub MCP write calls."""
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # change\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        for blocked_status in (RunStatus.NEEDS_HUMAN, RunStatus.BLOCKED):
            state = RunState(
                run_id=f"fail_{blocked_status.value}_run",
                requirement="Req",
                status=blocked_status,
                test_reports=[
                    TestReport(
                        command="pytest",
                        passed=True,
                        exit_code=0,
                        workspace_hash=ws_hash,
                    )
                ],
                review=ReviewReport(verdict=ReviewVerdict.APPROVE, summary="Approved"),
            )

            with patch("repoagent.pr_agent.call_tool_direct") as mock_mcp:
                with pytest.raises(ValueError, match=f"Run status is '{blocked_status.value}'"):
                    open_and_verify_pull_request(state, ws)
                mock_mcp.assert_not_called()

    def test_regression_valid_done_run_passes_gate_and_reaches_pr_creation(
        self, dummy_git_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression 6: A valid DONE run can still pass the gate and reach mocked PR creation."""
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # valid feature code\n", encoding="utf-8"
        )
        ws_hash = compute_workspace_hash(ws)

        state = RunState(
            run_id="valid_done_run",
            requirement="Add valid feature",
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
                summary="Approved",
                provenance="authoritative_adk_reviewer",
            ),
        )

        mock_remote_baseline = json.dumps(
            {
                "name": "auth.py",
                "path": "src/notes_app/auth.py",
                "content": "class AuthService:\n    pass\n",
            }
        )

        called_tools: list[str] = []

        async def fake_call_tool(
            params: Any, tool_name: str, arguments: dict[str, Any] | None = None
        ) -> str:
            called_tools.append(tool_name)
            args = arguments or {}
            if tool_name == "get_file_contents":
                return mock_remote_baseline
            if tool_name == "create_branch":
                return json.dumps({"ref": f"refs/heads/{args.get('branch')}"})
            if tool_name == "push_files":
                return json.dumps({"commit": "sha_push_regression"})
            if tool_name == "create_pull_request":
                return json.dumps(
                    {
                        "number": 888,
                        "html_url": "https://github.com/Kushagra-Pachauri/demo_repo/pull/888",
                        "state": "open",
                    }
                )
            if tool_name == "get_pull_request":
                return json.dumps(
                    {
                        "state": "open",
                        "base": {"ref": "main"},
                        "head": {"ref": args.get("head", f"repoagent/{state.run_id}")},
                        "html_url": "https://github.com/Kushagra-Pachauri/demo_repo/pull/888",
                    }
                )
            if tool_name == "get_pull_request_files":
                return json.dumps(
                    [
                        {
                            "filename": "src/notes_app/auth.py",
                            "status": "modified",
                            "additions": 3,
                            "deletions": 0,
                            "changes": 3,
                        }
                    ]
                )
            return "{}"

        monkeypatch.setattr("repoagent.pr_agent.call_tool_direct", fake_call_tool)
        monkeypatch.setenv("GITHUB_TOKEN", "mock_token")

        pr_summary = open_and_verify_pull_request(state, ws)

        assert pr_summary.pull_number == 888
        assert "create_branch" in called_tools
        assert "push_files" in called_tools
        assert "create_pull_request" in called_tools
