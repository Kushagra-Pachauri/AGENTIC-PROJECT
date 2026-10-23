"""CrewAI Testing Agent module.

Executes test and lint commands inside the Docker sandbox container using test-runner
MCP tools, returning a validated TestReport model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from crewai import Agent, Crew, Process, Task

from repoagent.schemas import TestReport

if TYPE_CHECKING:
    from crewai.tools.base_tool import BaseTool


def create_testing_agent(
    llm: Any,
    tools: list[BaseTool],
    verbose: bool = False,
) -> Agent:
    """Create the CrewAI Testing Agent configured with Docker sandbox MCP tools."""
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
        f"failed_tests, log_tail, and duration_s."
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
    verbose: bool = False,
) -> TestReport:
    """Run the Testing Agent synchronously and return a validated TestReport."""
    agent = create_testing_agent(llm=llm, tools=tools, verbose=verbose)
    task = create_testing_task(agent=agent, command=command)

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=verbose,
    )

    crew.kickoff()

    # Extract validated Pydantic model
    if task.output and task.output.pydantic and isinstance(task.output.pydantic, TestReport):
        return task.output.pydantic

    # Fallback parsing from JSON dict or raw output
    if task.output and task.output.json_dict:
        return TestReport.model_validate(task.output.json_dict)

    if task.output and task.output.raw:
        return TestReport.model_validate_json(task.output.raw)

    msg = "Testing Agent task did not produce a valid TestReport."
    raise ValueError(msg)
