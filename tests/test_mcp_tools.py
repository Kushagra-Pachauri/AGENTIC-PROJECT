"""Tests for RepoAgent MCP tools and client helpers.

Verifies:
  - Direct tool execution (repo_reader, test_runner)
  - Path traversal and absolute path rejection
  - Command allowlist validation and disallowed command rejection
  - Docker sandbox test execution and timeout enforcement
  - MCP client parameter builders, CrewAI adapter, and ADK toolset
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repoagent.mcp_client import (
    call_tool_direct,
    get_adk_toolset,
    get_crewai_mcp_server,
    get_crewai_tools,
    get_github_params,
    get_repo_reader_params,
    get_test_runner_params,
    list_tools_direct,
)
from repoagent.mcp_servers import repo_reader, test_runner
from repoagent.schemas import TestReport

# Path to the demo_repo fixture in the workspace
DEMO_REPO_PATH = Path(__file__).resolve().parent.parent / "demo_repo"


# ---------------------------------------------------------------------------
# Repo Reader Tests
# ---------------------------------------------------------------------------


class TestRepoReader:
    """Tests for repo_reader MCP server tools."""

    @pytest.fixture(autouse=True)
    def setup_workspace(self) -> None:
        repo_reader._set_workspace(str(DEMO_REPO_PATH))

    def test_list_tree_direct(self) -> None:
        tree = repo_reader.list_tree()
        assert "demo_repo/" in tree
        assert "src/" in tree
        assert "models.py" in tree

    def test_list_tree_nonexistent_dir(self) -> None:
        result = repo_reader.list_tree("nonexistent_folder")
        assert "Error:" in result
        assert "not a directory" in result

    def test_read_file_direct(self) -> None:
        content = repo_reader.read_file("src/notes_app/models.py")
        assert "class User(BaseModel):" in content
        assert "class Note(BaseModel):" in content

    def test_read_file_nonexistent(self) -> None:
        result = repo_reader.read_file("nonexistent.py")
        assert "Error:" in result
        assert "not a file" in result

    def test_read_file_truncation(self) -> None:
        content = repo_reader.read_file("src/notes_app/models.py", max_lines=5)
        assert len(content.splitlines()) <= 7  # 5 lines + truncation marker
        assert "truncated" in content

    def test_search_code_direct(self) -> None:
        results = repo_reader.search_code("class AuthService")
        assert "auth.py" in results
        assert "class AuthService" in results

    def test_search_code_no_match(self) -> None:
        results = repo_reader.search_code("nonexistent_symbol_xyz123")
        assert results == "No matches found."

    def test_path_traversal_relative_rejected(self) -> None:
        """Reject relative path traversal attempts like ../../"""
        with pytest.raises(ValueError, match="Path traversal detected"):
            repo_reader.read_file("../../pyproject.toml")

        with pytest.raises(ValueError, match="Path traversal detected"):
            repo_reader.list_tree("../..")

        with pytest.raises(ValueError, match="Path traversal detected"):
            repo_reader.search_code("pattern", path="../../")

    def test_absolute_path_rejected(self) -> None:
        """Reject absolute paths attempting to access system files."""
        with pytest.raises(ValueError, match="Absolute paths are not allowed"):
            repo_reader.read_file("/etc/passwd")

        with pytest.raises(ValueError, match="Absolute paths are not allowed"):
            repo_reader.read_file("C:/Windows/win.ini")

        with pytest.raises(ValueError, match="Absolute paths are not allowed"):
            repo_reader.list_tree("C:\\")

    def test_workspace_not_configured(self) -> None:
        original = repo_reader._WORKSPACE
        try:
            repo_reader._WORKSPACE = None
            with pytest.raises(RuntimeError, match="Workspace not configured"):
                repo_reader.read_file("README.md")
        finally:
            repo_reader._WORKSPACE = original


# ---------------------------------------------------------------------------
# Test Runner Tests
# ---------------------------------------------------------------------------


class TestTestRunner:
    """Tests for test_runner MCP server tools."""

    @pytest.fixture(autouse=True)
    def setup_workspace(self) -> None:
        test_runner._set_workspace(str(DEMO_REPO_PATH))

    def test_allowed_commands(self) -> None:
        """Verify that allowlisted commands are accepted."""
        assert test_runner._validate_command("pytest") == "pytest"
        assert test_runner._validate_command("pytest tests/ -v") == "pytest tests/ -v"
        assert test_runner._validate_command("ruff check .") == "ruff check ."
        assert test_runner._validate_command("npm test") == "npm test"
        assert test_runner._validate_command("eslint src/") == "eslint src/"

    def test_disallowed_commands_rejected(self) -> None:
        """Verify that non-allowlisted commands are rejected."""
        disallowed = [
            "python evil.py",
            "cat /etc/passwd",
            "rm -rf /",
            "bash -c 'whoami'",
            "pytest_fake_command",
            "echo hello",
        ]
        for cmd in disallowed:
            with pytest.raises(ValueError, match="Command not allowed"):
                test_runner._validate_command(cmd)

    def test_shell_metacharacters_rejected(self) -> None:
        """Verify that shell metacharacters are rejected even with valid prefixes."""
        malicious = [
            "pytest; rm -rf /",
            "pytest && cat /etc/passwd",
            "pytest | grep secret",
            "pytest `whoami`",
            "pytest $(id)",
            "pytest > output.txt",
        ]
        for cmd in malicious:
            with pytest.raises(ValueError, match="Shell metacharacters not allowed"):
                test_runner._validate_command(cmd)

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="Empty command"):
            test_runner._validate_command("   ")

    def test_run_tests_disallowed_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Command not allowed"):
            test_runner.run_tests("sh -c evil")

    def test_format_test_report_pass(self) -> None:
        raw_result = {
            "exit_code": 0,
            "stdout": "12 passed in 0.1s",
            "stderr": "",
            "duration_s": 0.5,
            "timed_out": False,
        }
        report = test_runner._format_test_report("pytest", raw_result)
        assert isinstance(report, TestReport)
        assert report.passed is True
        assert report.exit_code == 0
        assert report.failed_tests == []
        assert "12 passed" in report.log_tail

    def test_format_test_report_fail(self) -> None:
        raw_result = {
            "exit_code": 1,
            "stdout": "FAILED tests/test_auth.py::test_login - AssertionError\n1 failed",
            "stderr": "",
            "duration_s": 0.8,
            "timed_out": False,
        }
        report = test_runner._format_test_report("pytest", raw_result)
        assert isinstance(report, TestReport)
        assert report.passed is False
        assert report.exit_code == 1
        assert "tests/test_auth.py::test_login" in report.failed_tests

    def test_sandbox_timeout_enforced(self) -> None:
        """Verify that sandbox timeout marks the report as failed and notes [TIMEOUT]."""
        raw_result = {
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "duration_s": 10.0,
            "timed_out": True,
        }
        report = test_runner._format_test_report("pytest", raw_result)
        assert isinstance(report, TestReport)
        assert report.passed is False
        assert "[TIMEOUT]" in report.log_tail

    def test_run_tests_docker_sandbox_live(self) -> None:
        """Live test execution inside the built Docker sandbox container."""
        report = test_runner.run_tests("pytest")
        assert isinstance(report, TestReport)
        assert report.passed is True
        assert report.exit_code == 0
        assert "12 passed" in report.log_tail

    def test_run_lint_docker_sandbox_live(self) -> None:
        """Live lint execution inside the built Docker sandbox container."""
        report = test_runner.run_lint()
        assert isinstance(report, TestReport)
        assert report.passed is True
        assert report.exit_code == 0

    def test_run_in_docker_timeout_mocked(self) -> None:
        """Verify Docker timeout handling when container.wait raises."""
        mock_container = MagicMock()
        mock_container.wait.side_effect = Exception("Timed out")
        mock_container.logs.return_value = b""

        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container

        with patch("docker.from_env", return_value=mock_client):
            res = test_runner._run_in_docker("pytest", timeout_s=1)
            assert res["timed_out"] is True
            assert res["exit_code"] == -1
            mock_container.kill.assert_called_once()
            mock_container.remove.assert_called_once_with(force=True)


# ---------------------------------------------------------------------------
# MCP Client Helpers Tests
# ---------------------------------------------------------------------------


class TestMCPClient:
    """Tests for repoagent.mcp_client helpers."""

    def test_repo_reader_params(self) -> None:
        params = get_repo_reader_params(DEMO_REPO_PATH)
        assert "python" in params.command.lower()
        assert any("repo_reader.py" in arg for arg in params.args)
        assert params.env is not None
        assert "REPOAGENT_WORKSPACE" in params.env

    def test_test_runner_params(self) -> None:
        params = get_test_runner_params(DEMO_REPO_PATH)
        assert "python" in params.command.lower()
        assert any("test_runner.py" in arg for arg in params.args)
        assert params.env is not None
        assert "REPOAGENT_WORKSPACE" in params.env

    def test_github_params_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
        with pytest.raises(EnvironmentError, match="GITHUB_TOKEN environment variable"):
            get_github_params()

    def test_github_params_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token_12345")
        params = get_github_params()
        assert "npx" in params.command.lower()
        assert "@modelcontextprotocol/server-github" in params.args
        assert params.env is not None
        assert params.env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_test_token_12345"

    def test_get_crewai_mcp_server(self) -> None:
        params = get_repo_reader_params(DEMO_REPO_PATH)
        server_config = get_crewai_mcp_server(params)
        assert server_config.command == params.command
        assert server_config.args == params.args

    def test_get_crewai_tools(self) -> None:
        params = get_repo_reader_params(DEMO_REPO_PATH)
        tools = get_crewai_tools(params)
        tool_names = {t.name for t in tools}
        assert "list_tree" in tool_names
        assert "read_file" in tool_names
        assert "search_code" in tool_names

    def test_get_adk_toolset(self) -> None:
        try:
            from google.adk.tools.mcp_tool.mcp_toolset import McpToolset as ExpectedToolset
        except ImportError:
            from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset as ExpectedToolset

        params = get_repo_reader_params(DEMO_REPO_PATH)
        toolset = get_adk_toolset(params)
        assert isinstance(toolset, ExpectedToolset)

    @pytest.mark.asyncio
    async def test_direct_mcp_call_repo_reader(self) -> None:
        """Call repo_reader via raw MCP ClientSession over stdio."""
        params = get_repo_reader_params(DEMO_REPO_PATH)
        tools = await list_tools_direct(params)
        assert len(tools) >= 3

        result = await call_tool_direct(params, "read_file", {"path": "README.md"})
        assert "# Notes App" in result
