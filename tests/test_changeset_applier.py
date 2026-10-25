"""Unit tests for ChangeSet validation, application, and safety enforcement."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repoagent.changeset_applier import (
    apply_changeset_to_workspace,
    apply_file_change,
    apply_unified_diff,
    validate_safe_path,
    workspace_has_modifications,
)
from repoagent.flow import RepoAgentFlow
from repoagent.schemas import ChangeSet, FileChange, RunState, RunStatus, TestReport


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace initialized with a git baseline."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "src").mkdir()
    sample_code = "def hello() -> str:\n    return 'world'\n"
    (ws / "src" / "sample.py").write_text(sample_code, encoding="utf-8")

    # Git init and baseline commit
    subprocess.run(["git", "init"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "TestUser"],
        cwd=str(ws),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(ws),
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=str(ws), check=True, capture_output=True)
    return ws


class TestChangeSetApplier:
    """Tests for safe changeset application and path validation."""

    def test_valid_changeset_modifies_workspace_files(self, temp_workspace: Path) -> None:
        """Verify that applying a valid ChangeSet modifies actual file contents on disk."""
        target = temp_workspace / "src" / "sample.py"
        assert "foo" not in target.read_text(encoding="utf-8")

        diff_sample = (
            "@@ -1,2 +1,4 @@\n def hello() -> str:\n+    print('foo')\n     return 'world'\n"
        )
        changeset = ChangeSet(
            files=[
                FileChange(
                    path="src/sample.py",
                    action="modify",
                    diff=diff_sample,
                ),
                FileChange(
                    path="src/new_file.py",
                    action="create",
                    diff="def created() -> int:\n    return 42\n",
                ),
            ],
            summary="Add logging and helper",
        )

        modified = apply_changeset_to_workspace(changeset, temp_workspace)
        assert len(modified) == 2
        assert "print('foo')" in target.read_text(encoding="utf-8")
        assert (temp_workspace / "src" / "new_file.py").is_file()
        assert workspace_has_modifications(temp_workspace) is True

    def test_unsafe_paths_rejected(self, temp_workspace: Path) -> None:
        """Verify absolute paths and directory traversal are strictly rejected."""
        # Absolute path POSIX/Windows
        with pytest.raises(ValueError, match="Absolute paths are forbidden"):
            validate_safe_path(temp_workspace, "/etc/passwd")

        with pytest.raises(ValueError, match="Absolute paths are forbidden"):
            validate_safe_path(temp_workspace, "C:\\Windows\\System32")

        # Traversal with ..
        with pytest.raises(ValueError, match="Path traversal forbidden"):
            validate_safe_path(temp_workspace, "../outside.py")

        with pytest.raises(ValueError, match="Path traversal forbidden"):
            validate_safe_path(temp_workspace, "src/../../outside.py")

        # Empty path
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_safe_path(temp_workspace, "")

        # Targeting demo_repo directly
        demo_repo = Path("demo_repo").resolve()
        if demo_repo.is_dir():
            err_msg = "Cannot apply changes directly to baseline 'demo_repo'"
            with pytest.raises(ValueError, match=err_msg):
                validate_safe_path(demo_repo, "src/notes_app/auth.py")

    def test_unsupported_operation_rejected(self, temp_workspace: Path) -> None:
        """Verify unsupported operations raise ValueError."""
        change = FileChange(path="src/sample.py", action="rename", diff="some diff")
        with pytest.raises(ValueError, match="Unsupported operation 'rename'"):
            apply_file_change(temp_workspace, change)

    def test_malformed_diff_rejected(self, temp_workspace: Path) -> None:
        """Verify empty diff or invalid hunk raises ValueError."""
        # Empty diff
        change = FileChange(path="src/sample.py", action="modify", diff="   ")
        with pytest.raises(ValueError, match="Malformed diff: content is empty"):
            apply_file_change(temp_workspace, change)

        # Invalid hunk context with no additions
        bad_hunk = "@@ -50,5 +50,5 @@\n non_existent_context_line\n"
        with pytest.raises(ValueError, match="Failed to apply hunk context"):
            apply_unified_diff("def existing(): pass\n", bad_hunk)

    def test_regression_malformed_hunk_header_with_additions(self) -> None:
        """Regression test: malformed header like '@@ -54,6 +54,8@' applies additions."""
        orig = "class Service:\n    def run(self) -> bool:\n        return True\n"
        # Notice '@' instead of '@@' at end of hunk header
        malformed_diff = "@@ -54,6 +54,8@\n+    def extra(self) -> str:\n+        return 'extra'\n"
        res = apply_unified_diff(orig, malformed_diff)
        assert "def extra" in res
        assert "def run" in res

    def test_regression_missing_application_methods_injected_into_class(self) -> None:
        """Regression test: methods in diff are cleanly injected into target class body."""
        orig = (
            "class NotesApp:\n"
            "    def __init__(self) -> None:\n"
            "        self.name = 'demo'\n"
            "\n"
            "    def login(self, username: str) -> bool:\n"
            "        return True\n"
        )
        # Diff containing delegating methods with context that doesn't match original lines
        diff_with_methods = (
            "@@ -1,5 +1,10 @@\n"
            " non_existent_context_line_hallucinated\n"
            "+    def request_password_reset(self, username: str) -> str:\n"
            "+        return self.auth.request_password_reset(username)\n"
            "+\n"
            "+    def reset_password(self, username: str, token: str, new_password: str) -> bool:\n"
            "+        return self.auth.reset_password(username, token, new_password)\n"
        )
        res = apply_unified_diff(orig, diff_with_methods)
        assert "def request_password_reset" in res
        assert "def reset_password" in res
        # Verify valid Python syntax
        import ast

        tree = ast.parse(res)
        class_node = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NotesApp"
        )
        method_names = [n.name for n in class_node.body if isinstance(n, ast.FunctionDef)]
        assert "request_password_reset" in method_names
        assert "reset_password" in method_names
        assert "login" in method_names

    def test_regression_full_module_replacement_diff(self) -> None:
        """Regression test: model returns full updated file rather than diff hunks."""
        orig = "class Foo:\n    pass\n"
        full_replacement = (
            '"""Updated Foo module."""\n'
            "\n"
            "class Foo:\n"
            "    def bar(self) -> int:\n"
            "        return 100\n"
        )
        res = apply_unified_diff(orig, full_replacement)
        assert "class Foo" in res
        assert "def bar" in res
        assert '"""Updated Foo module."""' in res

    def test_tests_run_after_changes_applied(self, temp_workspace: Path) -> None:
        """Verify testing observes modified file contents."""
        observed_contents: list[str] = []

        def custom_coding(state: RunState) -> ChangeSet:
            return ChangeSet(
                files=[
                    FileChange(
                        path="src/sample.py",
                        action="modify",
                        diff="+def added_feature(): return True\n",
                    )
                ],
                summary="Added feature",
            )

        def custom_testing(state: RunState) -> TestReport:
            content = (temp_workspace / "src" / "sample.py").read_text(encoding="utf-8")
            observed_contents.append(content)
            return TestReport(
                command="pytest",
                passed=True,
                exit_code=0,
                failed_tests=[],
                log_tail="1 passed",
                duration_s=0.1,
            )

        flow = RepoAgentFlow(
            initial_state=RunState(requirement="Add feature", max_retries=1),
            workspace=temp_workspace,
            coding_fn=custom_coding,
            testing_fn=custom_testing,
        )
        result = flow.kickoff()

        assert result == "open_pr_completed"
        assert flow.state.status == RunStatus.DONE
        assert len(observed_contents) == 1
        assert "def added_feature(): return True" in observed_contents[0]

    def test_failed_application_cannot_produce_done(self, temp_workspace: Path) -> None:
        """Verify that an unchanged workspace produces a failed test step and needs_human."""

        def no_op_coding(state: RunState) -> ChangeSet:
            # Emits empty changeset, leaving workspace unmodified
            return ChangeSet(files=[], summary="No files modified")

        flow = RepoAgentFlow(
            initial_state=RunState(requirement="Add feature", max_retries=2),
            workspace=temp_workspace,
            coding_fn=no_op_coding,
        )
        result = flow.kickoff()

        # Must terminate with needs_human because no changes were applied to the workspace
        assert result == "needs_human_completed"
        assert flow.state.status == RunStatus.NEEDS_HUMAN
        assert flow.state.retry_count >= 2
        # Verify failure reports were captured
        assert len(flow.state.test_reports) > 0
        assert all(not r.passed for r in flow.state.test_reports)
        assert any(
            "no_changes_applied" in t for r in flow.state.test_reports for t in r.failed_tests
        )
