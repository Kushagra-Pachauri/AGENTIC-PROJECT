"""Tests for Docker test-runner error handling, container execution, and provenance remediation."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from repoagent.done_gate import evaluate_done_gate
from repoagent.mcp_servers import test_runner
from repoagent.schemas import (
    ChangeSet,
    FileChange,
    Plan,
    PlannedFile,
    ReviewReport,
    ReviewVerdict,
    RunState,
    RunStatus,
    TestReport,
)
from repoagent.workspace_utils import compute_workspace_hash


@pytest.fixture
def temp_workspace() -> Path:
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        test_runner._set_workspace(str(ws))
        yield ws


class TestDockerTestRunnerProvenanceRemediation:
    """Verifies that Docker test runner assigns authoritative provenance only upon container run."""

    def test_docker_daemon_unavailable_during_from_env(self, temp_workspace: Path) -> None:
        """When Docker daemon is down during docker.from_env(), return structured failure."""
        err = docker.errors.DockerException("Daemon pipe not found")
        with patch("docker.from_env", side_effect=err):
            report = test_runner.run_tests("pytest")

        assert isinstance(report, TestReport)
        assert report.passed is False
        assert report.exit_code == -1
        assert "docker_startup_failure" in report.failed_tests[0]
        assert report.provenance != "authoritative_docker_sandbox"
        assert report.provenance == "docker_startup_failure"
        assert (
            "Docker initialization failed" in report.log_tail
            or "Docker initialization failed" in report.raw_output
        )

    def test_docker_image_missing(self, temp_workspace: Path) -> None:
        """When configured image is missing, return structured failure with non-authoritative."""
        mock_client = MagicMock()
        mock_client.containers.run.side_effect = docker.errors.ImageNotFound(
            "Image repoagent-sandbox:latest not found"
        )

        with patch("docker.from_env", return_value=mock_client):
            report = test_runner.run_tests("pytest")

        assert isinstance(report, TestReport)
        assert report.passed is False
        assert report.exit_code == -1
        assert "docker_startup_failure" in report.failed_tests[0]
        assert report.provenance != "authoritative_docker_sandbox"
        assert report.provenance == "docker_startup_failure"
        assert "Docker image not found" in report.log_tail

    def test_docker_container_creation_failure_before_execution(self, temp_workspace: Path) -> None:
        """When container creation fails with APIError, return structured non-authoritative."""
        mock_client = MagicMock()
        mock_client.containers.run.side_effect = docker.errors.APIError(
            "Resource constraint: failed to create container"
        )

        with patch("docker.from_env", return_value=mock_client):
            report = test_runner.run_tests("pytest")

        assert isinstance(report, TestReport)
        assert report.passed is False
        assert report.exit_code == -1
        assert "docker_startup_failure" in report.failed_tests[0]
        assert report.provenance != "authoritative_docker_sandbox"
        assert report.provenance == "docker_startup_failure"
        assert "Docker container creation failed" in report.log_tail

    def test_genuine_docker_pytest_pass(self, temp_workspace: Path) -> None:
        """When container executes pytest and passes, return passed=True with authoritative prov."""
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = (
            lambda stdout, stderr: b"5 passed in 0.15s\n" if stdout else b""
        )

        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container

        with patch("docker.from_env", return_value=mock_client):
            report = test_runner.run_tests("pytest")

        assert isinstance(report, TestReport)
        assert report.passed is True
        assert report.exit_code == 0
        assert report.failed_tests == []
        assert report.provenance == "authoritative_docker_sandbox"
        assert "5 passed in 0.15s" in report.log_tail

    def test_genuine_docker_pytest_assertion_failure(self, temp_workspace: Path) -> None:
        """When container executes pytest and fails, return passed=False with authoritative prov."""
        mock_container_compile = MagicMock()
        mock_container_compile.wait.return_value = {"StatusCode": 0}
        mock_container_compile.logs.return_value = b""

        mock_container_ruff = MagicMock()
        mock_container_ruff.wait.return_value = {"StatusCode": 0}
        mock_container_ruff.logs.return_value = b""

        mock_container_pytest = MagicMock()
        mock_container_pytest.wait.return_value = {"StatusCode": 1}
        mock_container_pytest.logs.side_effect = lambda stdout, stderr: (
            b"FAILED tests/test_calc.py::test_add - AssertionError\n1 failed in 0.20s\n"
            if stdout
            else b""
        )

        mock_client = MagicMock()
        mock_client.containers.run.side_effect = [
            mock_container_compile,
            mock_container_ruff,
            mock_container_pytest,
        ]

        with patch("docker.from_env", return_value=mock_client):
            report = test_runner.run_tests("pytest")

        assert isinstance(report, TestReport)
        assert report.passed is False
        assert report.exit_code == 1
        assert "tests/test_calc.py::test_add" in report.failed_tests
        assert report.provenance == "authoritative_docker_sandbox"

    def test_genuine_docker_timeout_execution_failure(self, temp_workspace: Path) -> None:
        """When container wait times out, return passed=False with authoritative provenance."""
        mock_container = MagicMock()
        mock_container.wait.side_effect = Exception("Container wait timed out")
        mock_container.logs.return_value = b"Running long test suite..."

        mock_client = MagicMock()
        mock_client.containers.run.return_value = mock_container

        with patch("docker.from_env", return_value=mock_client):
            report = test_runner.run_tests("pytest")

        assert isinstance(report, TestReport)
        assert report.passed is False
        assert report.exit_code == -1
        assert "[TIMEOUT]" in report.log_tail
        assert report.provenance == "authoritative_docker_sandbox"
        mock_container.kill.assert_called_once()
        mock_container.remove.assert_called_once_with(force=True)

    def test_run_lint_docker_startup_failure(self, temp_workspace: Path) -> None:
        """When Docker daemon is unavailable, run_lint returns structured non-authoritative."""
        err = docker.errors.DockerException("Daemon pipe not found")
        with patch("docker.from_env", side_effect=err):
            report = test_runner.run_lint()

        assert isinstance(report, TestReport)
        assert report.passed is False
        assert report.exit_code == -1
        assert report.provenance != "authoritative_docker_sandbox"
        assert report.provenance == "docker_startup_failure"

    def test_simulated_startup_failure_rejected_by_done_gate(self, temp_workspace: Path) -> None:
        """TestReport from Docker startup failure is rejected by DONE gate."""
        subprocess.run(["git", "init"], cwd=str(temp_workspace), capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(temp_workspace),
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(temp_workspace),
            capture_output=True,
            check=True,
        )
        (temp_workspace / "README.md").write_text("# Test\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "README.md"], cwd=str(temp_workspace), capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(temp_workspace),
            capture_output=True,
            check=True,
        )

        src_dir = temp_workspace / "src" / "notes_app"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "word_count.py").write_text(
            "def word_count(text): return len(text.split())\n", encoding="utf-8"
        )

        ws_hash = compute_workspace_hash(temp_workspace)

        startup_failure_report = TestReport(
            command="pytest",
            passed=False,
            exit_code=-1,
            failed_tests=["pytest:docker_startup_failure"],
            provenance="docker_startup_failure",
            workspace_hash=ws_hash,
        )

        review = ReviewReport(
            verdict=ReviewVerdict.APPROVE,
            summary="Approved",
            provenance="authoritative_adk_reviewer",
            findings=[],
        )

        plan = Plan(
            summary="Plan",
            files_to_change=[PlannedFile(path="src/notes_app/word_count.py", reason="Add utility")],
        )

        changes = ChangeSet(
            summary="Add word_count",
            changes=[
                FileChange(
                    path="src/notes_app/word_count.py",
                    action="create",
                    diff="+def word_count(text):\n",
                )
            ],
            file_hashes_after={"src/notes_app/word_count.py": "hash"},
        )

        state = RunState(
            run_id="test-startup-failure-gate",
            repo_url="https://github.com/demo/repo",
            requirement="Add word_count(text)",
            plan=plan,
            changes=changes,
            test_reports=[startup_failure_report],
            test_result=startup_failure_report,
            review=review,
            review_result=review,
            status=RunStatus.RUNNING,
            workspace_dir=str(temp_workspace),
            isolated_workspace_dir=str(temp_workspace),
        )

        passed, failure = evaluate_done_gate(state, workspace=temp_workspace, is_mock_test=False)
        assert passed is False
        assert failure is not None
        assert "Latest test report missing, failed, or non-zero exit code" in failure


class TestExplicitExecutionEvidenceGating:
    """Verifies that only explicit executed_in_container=True qualifies for authoritative prov."""

    def test_missing_execution_flag_with_exit_code_zero(self) -> None:
        """When executed_in_container is missing, report MUST fail closed."""
        raw_result = {
            "exit_code": 0,
            "stdout": "5 passed in 0.1s",
            "stderr": "",
            "duration_s": 0.5,
            "timed_out": False,
        }
        report = test_runner._format_test_report("pytest", raw_result)
        assert report.passed is False
        assert report.provenance != "authoritative_docker_sandbox"
        assert report.provenance == "docker_startup_failure"
        assert "docker_startup_failure" in report.failed_tests[0]

    def test_none_execution_flag_with_exit_code_zero(self) -> None:
        """When executed_in_container is None, report MUST fail closed."""
        raw_result = {
            "exit_code": 0,
            "stdout": "5 passed in 0.1s",
            "stderr": "",
            "duration_s": 0.5,
            "timed_out": False,
            "executed_in_container": None,
        }
        report = test_runner._format_test_report("pytest", raw_result)
        assert report.passed is False
        assert report.provenance != "authoritative_docker_sandbox"
        assert report.provenance == "docker_startup_failure"
        assert "docker_startup_failure" in report.failed_tests[0]

    def test_false_execution_flag_with_exit_code_zero(self) -> None:
        """When executed_in_container is False, report MUST fail closed."""
        raw_result = {
            "exit_code": 0,
            "stdout": "5 passed in 0.1s",
            "stderr": "",
            "duration_s": 0.5,
            "timed_out": False,
            "executed_in_container": False,
        }
        report = test_runner._format_test_report("pytest", raw_result)
        assert report.passed is False
        assert report.provenance != "authoritative_docker_sandbox"
        assert report.provenance == "docker_startup_failure"
        assert "docker_startup_failure" in report.failed_tests[0]

    def test_arbitrary_truthy_execution_flag_with_exit_code_zero(self) -> None:
        """When executed_in_container is a truthy non-boolean, it must NOT qualify as True."""
        for truthy_val in ("true", 1, ["True"], {"executed": True}):
            raw_result = {
                "exit_code": 0,
                "stdout": "5 passed in 0.1s",
                "stderr": "",
                "duration_s": 0.5,
                "timed_out": False,
                "executed_in_container": truthy_val,
            }
            report = test_runner._format_test_report("pytest", raw_result)
            assert report.passed is False
            assert report.provenance != "authoritative_docker_sandbox"
            assert report.provenance == "docker_startup_failure"

    def test_true_execution_flag_with_exit_code_zero(self) -> None:
        """When executed_in_container is True and exit_code=0, report passes with auth prov."""
        raw_result = {
            "exit_code": 0,
            "stdout": "5 passed in 0.1s",
            "stderr": "",
            "duration_s": 0.5,
            "timed_out": False,
            "executed_in_container": True,
        }
        report = test_runner._format_test_report("pytest", raw_result)
        assert report.passed is True
        assert report.exit_code == 0
        assert report.failed_tests == []
        assert report.provenance == "authoritative_docker_sandbox"

    def test_true_execution_flag_with_exit_code_one(self) -> None:
        """When executed_in_container is True and exit_code=1, report fails with auth prov."""
        raw_result = {
            "exit_code": 1,
            "stdout": "FAILED tests/test_calc.py::test_math - AssertionError\n1 failed",
            "stderr": "",
            "duration_s": 0.5,
            "timed_out": False,
            "executed_in_container": True,
        }
        report = test_runner._format_test_report("pytest", raw_result)
        assert report.passed is False
        assert report.exit_code == 1
        assert "tests/test_calc.py::test_math" in report.failed_tests
        assert report.provenance == "authoritative_docker_sandbox"
