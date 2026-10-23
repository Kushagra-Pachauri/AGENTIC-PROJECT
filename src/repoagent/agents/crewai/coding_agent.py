"""CrewAI Coding Agent module.

Implements code changes using repo-reader/writer MCP tools and outputs a validated ChangeSet.
Supports correction iterations by incorporating failure context from previous TestReports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from crewai import Agent, Crew, Process, Task

from repoagent.schemas import ChangeSet, Plan, TestReport

if TYPE_CHECKING:
    from crewai.tools.base_tool import BaseTool


def create_coding_agent(
    llm: Any,
    tools: list[BaseTool],
    verbose: bool = False,
) -> Agent:
    """Create the CrewAI Coding Agent configured with repository MCP tools."""
    return Agent(
        role="Senior Python Engineer",
        goal="Implement software changes according to plan and fix test regressions",
        backstory=(
            "You are a meticulous senior Python software engineer. You read repository "
            "files, analyze requirements and plans, write clean Python code with Pydantic "
            "and pytest standards, and fix any test failures or bugs reported in prior runs."
        ),
        tools=tools,
        llm=llm,
        verbose=verbose,
    )


def create_coding_task(
    agent: Agent,
    plan: Plan,
    requirement: str,
    failure_context: TestReport | None = None,
    iteration: int = 0,
) -> Task:
    """Create a coding task with output_pydantic=ChangeSet."""
    files_str = "\n".join(f"- {f.path}: {f.reason}" for f in plan.files_to_change)
    steps_str = "\n".join(f"{s}" for s in plan.steps)

    description = (
        f"Requirement: {requirement}\n\n"
        f"Plan Summary: {plan.summary}\n"
        f"Files to Change:\n{files_str}\n\n"
        f"Execution Steps:\n{steps_str}\n\n"
        f"Test Strategy: {plan.test_strategy}\n\n"
        "Instructions:\n"
        "1. Use the 'read_file' tool to inspect existing code before making edits.\n"
        "2. Use the 'write_file' tool to write updated or new files into the repository.\n"
        "3. Produce clean, well-tested code that maintains backwards compatibility.\n"
    )

    if failure_context is not None and not failure_context.passed:
        failed_list = ", ".join(failure_context.failed_tests) or "Non-zero exit code"
        description += (
            f"\n--- CORRECTION ITERATION {iteration} ---\n"
            f"The previous test attempt failed!\n"
            f"Failed Tests: {failed_list}\n"
            f"Test Log Tail:\n{failure_context.log_tail}\n\n"
            "Analyze the failure log above and apply the necessary fixes using write_file.\n"
        )

    expected_output = (
        "A valid ChangeSet object listing every file changed, action taken (create/modify/delete), "
        "the diff or summary of changes, and an overall summary of the implementation."
    )

    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
        output_pydantic=ChangeSet,
    )


def run_coding_agent(
    plan: Plan,
    requirement: str,
    tools: list[BaseTool],
    llm: Any,
    failure_context: TestReport | None = None,
    iteration: int = 0,
    verbose: bool = False,
) -> ChangeSet:
    """Run the Coding Agent synchronously and return a validated ChangeSet."""
    agent = create_coding_agent(llm=llm, tools=tools, verbose=verbose)
    task = create_coding_task(
        agent=agent,
        plan=plan,
        requirement=requirement,
        failure_context=failure_context,
        iteration=iteration,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=verbose,
    )

    crew.kickoff()

    # Extract validated Pydantic model
    if task.output and task.output.pydantic and isinstance(task.output.pydantic, ChangeSet):
        return task.output.pydantic

    # Fallback parsing from JSON dict or raw output
    if task.output and task.output.json_dict:
        return ChangeSet.model_validate(task.output.json_dict)

    if task.output and task.output.raw:
        return ChangeSet.model_validate_json(task.output.raw)

    msg = "Coding Agent task did not produce a valid ChangeSet."
    raise ValueError(msg)
