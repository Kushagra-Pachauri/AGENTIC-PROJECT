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

    @pytest.mark.asyncio
    async def test_baseline_mismatch_prevents_branch_creation(
        self, dummy_git_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that a remote baseline mismatch stops execution before any branch is created."""
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # modified\n", encoding="utf-8"
        )

        state = RunState(run_id="mismatch_run", requirement="Req")

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

    @pytest.mark.asyncio
    async def test_pr_creation_and_verification_success(
        self, dummy_git_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify successful PR creation, atomic file push, and post-creation verification."""
        ws = dummy_git_workspace
        (ws / "src" / "notes_app" / "auth.py").write_text(
            "class AuthService:\n    # feature added\n", encoding="utf-8"
        )

        state = RunState(run_id="success_run", requirement="Add password reset")

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

        state = RunState(run_id="verif_fail", requirement="Req")

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

        state = RunState(
            run_id="offline_pass",
            requirement="Req",
            test_reports=[
                TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash="mock_hash",
                )
            ],
            review=ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                summary="Approved",
            ),
        )

        with patch("repoagent.pr_agent.open_and_verify_pull_request") as mock_open_pr:
            flow = RepoAgentFlow(
                initial_state=state,
                workspace=ws,
                create_pr=False,
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
