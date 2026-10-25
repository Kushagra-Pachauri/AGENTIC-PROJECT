"""CrewAI Testing Agent module.

Executes test and lint commands inside the Docker sandbox container using test-runner
MCP tools, returning a validated TestReport model.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from crewai import Agent, Crew, Process, Task
from repoagent.schemas import TestReport

if TYPE_CHECKING:
    from crewai.tools.base_tool import BaseTool


def create_testing_agent(
    llm: Any,
    tools: list[BaseTool],
    max_rpm: int | None = None,
    verbose: bool = False,
) -> Agent:
    """Create the CrewAI Testing Agent configured with Docker sandbox MCP tools."""
    rpm = max_rpm or int(os.environ.get("REPOAGENT_MAX_RPM", "4"))
    return Agent(
        role="QA Automation Engineer",
        goal="Run tests and linting in the sandbox and report accurate TestReport results",
        backstory=(
            "You are an expert QA and test automation engineer. You run test commands "
            "like pytest and ruff inside secure containerized environments, inspect exit codes "
            "and failure logs, and produce precise test reports for engineering teams."
        ),
        tools=tools,
        llm=llm,
        max_rpm=rpm,
        verbose=verbose,
    )


def create_testing_task(
    agent: Agent,
    command: str = "pytest",
) -> Task:
    """Create a testing task with output_pydantic=TestReport."""
    description = (
        f"Execute the test suite inside the Docker sandbox container.\n"
        f"1. Use the 'run_tests' tool with command='{command}'.\n"
        f"2. Check if all tests pass. If tests fail, capture the failed test names and log tail.\n"
        f"3. Return the exact TestReport output with command, passed, exit_code, "
        f"failed_tests, log_tail, and duration_s.\n"
        "CRITICAL: Do NOT invent synthetic test failures or mock test file names. "
        "Report only the actual output from the 'run_tests' tool."
    )

    expected_output = (
        "A valid TestReport object containing command, passed boolean, exit_code integer, "
        "failed_tests list, log_tail string, and duration_s float."
    )

    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
        output_pydantic=TestReport,
    )


def run_testing_agent(
    tools: list[BaseTool],
    llm: Any,
    command: str = "pytest",
    max_rpm: int | None = None,
    verbose: bool = False,
) -> TestReport:
    """Run the Testing Agent synchronously and return a validated TestReport."""
    rpm = max_rpm or int(os.environ.get("REPOAGENT_MAX_RPM", "4"))
    agent = create_testing_agent(llm=llm, tools=tools, max_rpm=rpm, verbose=verbose)
    task = create_testing_task(agent=agent, command=command)

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        max_rpm=rpm,
        verbose=verbose,
    )

    crew.kickoff()

    # Extract validated Pydantic model
    report: TestReport | None = None
    if task.output and task.output.pydantic and isinstance(task.output.pydantic, TestReport):
        report = task.output.pydantic
    elif task.output and task.output.json_dict:
        report = TestReport.model_validate(task.output.json_dict)
    elif task.output and task.output.raw:
        report = TestReport.model_validate_json(task.output.raw)

    # Sanity check: reject synthetic hallucinated test reports
    if report is not None:
        has_mock_names = any(
            "test_failed_file" in t or "test_name_" in t for t in report.failed_tests
        )
        if not has_mock_names:
            return report

    # Direct fallback: execute run_tests tool if model returned placeholders
    for tool in tools:
        if tool.name == "run_tests":
            try:
                res = tool._run(command=command)
                if isinstance(res, dict):
                    return TestReport(
                        command=command,
                        passed=res.get("passed", False),
                        exit_code=res.get("exit_code", 1),
                        failed_tests=res.get("failed_tests", []),
                        log_tail=res.get("stdout", "")[-2000:],
                        duration_s=float(res.get("duration_s", 0.0)),
                    )
            except Exception:
                pass

    if report is not None:
        return report

    msg = "Testing Agent task did not produce a valid TestReport."
    raise ValueError(msg)
