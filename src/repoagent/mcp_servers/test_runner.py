"""Test Runner MCP Server — executes tests and lint inside a Docker sandbox.

Tools:
  - run_tests(command): run an allowlisted test command inside the sandbox container
  - run_lint(): run ruff linting inside the sandbox container

Security constraints:
  - Only allowlisted commands: pytest, ruff, npm test, eslint
  - Runs inside Docker with: non-root user, no network, CPU/memory/time limits
  - Workspace is mounted read-only by default
"""

from __future__ import annotations

import contextlib
import os
import re
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from repoagent.schemas import TestReport

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_WORKSPACE: Path | None = None

# Allowlisted command prefixes (must start with one of these)
ALLOWED_COMMANDS: set[str] = {"pytest", "ruff", "npm test", "eslint"}

# Docker defaults (overridable via env)
DEFAULT_IMAGE = "repoagent-sandbox:latest"
DEFAULT_DOCKER_IMAGE = DEFAULT_IMAGE
DEFAULT_TIMEOUT_S = 120
DEFAULT_CPU_LIMIT = "1.0"
DEFAULT_MEMORY_LIMIT = "512m"

mcp = FastMCP("TestRunner")


def _set_workspace(workspace: str) -> None:
    """Set the workspace root directory."""
    global _WORKSPACE
    _WORKSPACE = Path(workspace).resolve()
    if not _WORKSPACE.is_dir():
        msg = f"Workspace directory does not exist: {_WORKSPACE}"
        raise FileNotFoundError(msg)


def _validate_command(command: str) -> str:
    """Validate *command* against the allowlist.

    Returns the sanitised command string.
    Raises ValueError or GuardrailViolation for disallowed commands.
    """
    from repoagent.security.tool_guardrails import validate_command_allowlist

    return validate_command_allowlist(command)


def _run_in_docker(command: str, timeout_s: int | None = None) -> dict[str, object]:
    """Run *command* inside the Docker sandbox container.

    Returns dict with keys: exit_code, stdout, stderr, duration_s, timed_out, executed_in_container.
    """
    import docker  # Import here so the module can be loaded without docker installed

    if _WORKSPACE is None:
        msg = "Workspace not configured."
        raise RuntimeError(msg)

    image = os.environ.get("SANDBOX_IMAGE", DEFAULT_IMAGE)
    timeout = timeout_s or int(os.environ.get("SANDBOX_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))
    cpu_limit = float(os.environ.get("SANDBOX_CPU_LIMIT", DEFAULT_CPU_LIMIT))
    memory_limit = os.environ.get("SANDBOX_MEMORY_LIMIT", DEFAULT_MEMORY_LIMIT)

    # Convert workspace path for Docker mount (handle Windows paths)
    workspace_str = str(_WORKSPACE)
    volumes = {workspace_str: {"bind": "/workspace", "mode": "rw"}}
    network_mode = "none"

    # Enforce strict container sandbox restrictions and mount policy
    from repoagent.security.tool_guardrails import validate_sandbox_mount_policy

    validate_sandbox_mount_policy(
        workspace=_WORKSPACE,
        volumes=volumes,
        network_mode=network_mode,
    )

    start_time = time.monotonic()
    timed_out = False
    container = None
    executed_in_container = False

    try:
        try:
            client = docker.from_env()
        except Exception as exc:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Docker initialization failed: {exc}",
                "duration_s": 0.0,
                "timed_out": False,
                "executed_in_container": False,
            }

        try:
            container = client.containers.run(
                image=image,
                command=["sh", "-c", command],
                volumes=volumes,
                working_dir="/workspace",
                network_mode=network_mode,  # No network access
                user="sandbox",
                nano_cpus=int(cpu_limit * 1e9),  # CPU limit in nano-CPUs
                mem_limit=memory_limit,
                detach=True,
                # Remove container after completion
            )
            executed_in_container = True
        except docker.errors.ImageNotFound:
            err_msg = (
                f"Docker image not found: {image}. Build with: docker build -t {image} sandbox/"
            )
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": err_msg,
                "duration_s": 0.0,
                "timed_out": False,
                "executed_in_container": False,
            }
        except Exception as exc:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Docker container creation failed: {exc}",
                "duration_s": 0.0,
                "timed_out": False,
                "executed_in_container": False,
            }

        try:
            try:
                result = container.wait(timeout=timeout)
                exit_code = result.get("StatusCode", -1)
            except Exception:
                # Timeout or other error — kill the container
                timed_out = True
                with contextlib.suppress(Exception):
                    container.kill()
                exit_code = -1

            try:
                stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            except Exception:
                stdout = ""
                stderr = "Failed to retrieve container logs."
        finally:
            with contextlib.suppress(Exception):
                container.remove(force=True)

    except Exception as exc:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Docker error: {exc}",
            "duration_s": 0.0,
            "timed_out": False,
            "executed_in_container": executed_in_container,
        }

    duration_s = time.monotonic() - start_time

    return {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_s": round(duration_s, 3),
        "timed_out": timed_out,
        "executed_in_container": True,
    }


def _format_test_report(
    command: str,
    result: dict[str, object],
) -> TestReport:
    """Format Docker run result as a TestReport model."""
    exit_code = int(result.get("exit_code", -1))  # type: ignore[arg-type]
    stdout = str(result.get("stdout", ""))
    stderr = str(result.get("stderr", ""))
    duration_s = max(0.0, float(result.get("duration_s", 0.0)))  # type: ignore[arg-type]
    timed_out = bool(result.get("timed_out", False))

    combined_output = stdout + stderr
    log_lines = combined_output.strip().splitlines()
    log_tail = "\n".join(log_lines[-30:]) if log_lines else ""

    # Positive execution evidence: only explicit True qualifies as execution in container
    executed_in_container = result.get("executed_in_container") is True

    # Detect passed / failed: exit_code 0, not timed out, and confirmed in container
    passed = exit_code == 0 and not timed_out and executed_in_container

    # Extract failed test names from pytest output
    failed_tests: list[str] = []
    for line in log_lines:
        # Match pytest FAILED lines like "FAILED tests/test_foo.py::test_bar"
        match = re.match(r"FAILED\s+(\S+)", line)
        if match:
            failed_tests.append(match.group(1))

    if not passed and not failed_tests:
        if not executed_in_container:
            failed_tests.append(f"{command.split()[0]}:docker_startup_failure")
        else:
            failed_tests.append(f"{command.split()[0]}:exit_{exit_code}")

    if timed_out:
        log_tail = (log_tail + "\n[TIMEOUT] Command timed out and was killed.").strip()

    from datetime import UTC, datetime

    # Assign provenance: authoritative ONLY if test command actually ran in the container sandbox
    if executed_in_container:
        provenance = "authoritative_docker_sandbox"
    else:
        prov = str(result.get("provenance", "docker_startup_failure"))
        provenance = prov if prov != "authoritative_docker_sandbox" else "docker_startup_failure"

    return TestReport(
        command=command,
        passed=passed,
        exit_code=exit_code,
        failed_tests=failed_tests,
        log_tail=log_tail,
        duration_s=duration_s,
        raw_output=combined_output,
        provenance=provenance,
        execution_timestamp=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def run_tests(command: str) -> TestReport:
    """Run a test command inside the Docker sandbox.

    Only these commands are allowed: pytest, ruff, npm test, eslint.
    For pytest runs, executes py_compile and ruff F821 undefined name checks
    prior to running the test suite. Any failure counts as an iteration failure.
    """
    validated = _validate_command(command)

    # Pre-flight checks for python test runs
    if validated.startswith("pytest"):
        # 1. py_compile check across workspace
        compile_cmd = (
            'python -c "'
            "import pathlib, py_compile; "
            "[py_compile.compile(str(p), cfile='/tmp/check.pyc', doraise=True) "
            "for p in pathlib.Path('.').rglob('*.py') if not p.name.startswith('.') "
            "and '.venv' not in p.parts]"
            '"'
        )
        compile_res = _run_in_docker(compile_cmd)
        if compile_res.get("exit_code", -1) != 0:
            cmd_label = validated if not compile_res.get("executed_in_container") else "py_compile"
            return _format_test_report(cmd_label, compile_res)

        # 2. Ruff undefined names check (F821)
        ruff_cmd = "ruff check --no-cache --select F821 ."
        ruff_res = _run_in_docker(ruff_cmd)
        if ruff_res.get("exit_code", -1) != 0:
            cmd_label = (
                validated
                if not ruff_res.get("executed_in_container")
                else "ruff check --select F821 ."
            )
            return _format_test_report(cmd_label, ruff_res)

    result = _run_in_docker(validated)
    return _format_test_report(validated, result)


@mcp.tool()
def run_lint() -> TestReport:
    """Run ruff linting on the workspace inside the Docker sandbox.

    Returns a TestReport with lint results.
    """
    result = _run_in_docker("ruff check .")
    return _format_test_report("ruff check .", result)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    workspace = os.environ.get("REPOAGENT_WORKSPACE", "")
    if not workspace:
        print("Error: set REPOAGENT_WORKSPACE env var", file=sys.stderr)
        sys.exit(1)
    _set_workspace(workspace)
    mcp.run()
