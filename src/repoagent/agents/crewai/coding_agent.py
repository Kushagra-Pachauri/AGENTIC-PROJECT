"""CrewAI Coding Agent module.

Implements code changes using repo-reader/writer MCP tools and outputs a validated ChangeSet.
Supports correction iterations by incorporating failure context from previous TestReports.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crewai import Agent, Crew, Process, Task
from repoagent.monitoring import (
    EventType,
    create_crewai_after_kickoff,
    create_crewai_before_kickoff,
    create_crewai_task_callback,
    get_event_logger,
    instrument_crewai_tools,
    resolve_run_id,
)
from repoagent.schemas import ChangeSet, Plan, TestReport

if TYPE_CHECKING:
    from crewai.tools.base_tool import BaseTool


def create_coding_agent(
    llm: Any,
    tools: list[BaseTool],
    max_rpm: int | None = None,
    verbose: bool = False,
    step_callback: Callable[[Any], None] | None = None,
    run_id: str | None = None,
) -> Agent:
    """Create the CrewAI Coding Agent configured with repository MCP tools."""
    rpm = max_rpm or int(os.environ.get("REPOAGENT_MAX_RPM", "4"))
    agent_kwargs: dict[str, Any] = {
        "role": "Senior Python Engineer",
        "goal": "Implement software changes according to plan and fix test regressions",
        "backstory": (
            "You are a meticulous senior Python software engineer. You read repository "
            "files, analyze requirements and plans, write clean Python code with Pydantic "
            "and pytest standards, and fix any test failures or bugs reported in prior runs.\n"
            "CRITICAL TOOL INSTRUCTION: When calling `read_file`, you must ONLY use parameters: "
            "`path` (str), `offset` (int, 0-indexed line start), and `limit` (int, max lines). "
            "NEVER use `line_start` or `line_end`."
        ),
        "tools": tools,
        "llm": llm,
        "max_rpm": rpm,
        "verbose": verbose,
    }
    if step_callback is not None:
        agent_kwargs["step_callback"] = step_callback
    return Agent(**agent_kwargs)


def create_coding_task(
    agent: Agent,
    plan: Plan,
    requirement: str,
    failure_context: TestReport | None = None,
    iteration: int = 0,
    workspace: Path | str | None = None,
    callback: Callable[[Any], None] | None = None,
    run_id: str | None = None,
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
        "TOOL CALL SCHEMA REQUIREMENTS:\n"
        "- When using `read_file`, pass strictly: `path` (str), `offset` (int, 0-indexed), and `limit` (int).\n"
        "- NEVER pass `line_start` or `line_end`.\n\n"
    )

    # If workspace is provided, include actual existing code context
    if workspace:
        ws_path = Path(workspace).resolve()
        code_context: list[str] = []
        for f in plan.files_to_change:
            target_path = ws_path / f.path
            if target_path.is_file():
                try:
                    text = target_path.read_text(encoding="utf-8")
                    from repoagent.security import screen_repository_content

                    screening = screen_repository_content(text, file_path=f.path, run_id=run_id)
                    if screening.is_suspicious and screening.sanitized_content:
                        text = screening.sanitized_content
                    code_context.append(f"--- Existing {f.path} ---\n{text}")
                except Exception:
                    pass
        if code_context:
            formatted_context = "\n\n".join(code_context)
            description += f"Existing Target Files in Repository:\n{formatted_context}\n\n"

    description += (
        "Implementation Guidelines:\n"
        f"1. Follow the planned architecture steps for requirement: '{requirement}'.\n"
        "2. Modify only the necessary files under 'src/notes_app/' and 'tests/'.\n"
        "3. Preserve all existing classes, methods, and functions unless explicitly "
        "instructed to update them.\n"
        "4. Do NOT create, edit, or overwrite tests/conftest.py or import non-existent modules.\n"
        "5. In ChangeSet output, for each file provide unified diff with '+' additions "
        "or complete updated code.\n"
    )

    if failure_context is not None and not failure_context.passed:
        failed_list = ", ".join(failure_context.failed_tests) or "Non-zero exit code"
        description += (
            f"\n--- CORRECTION ITERATION {iteration} ---\n"
            f"The previous test attempt failed!\n"
            f"Failed Tests: {failed_list}\n"
            f"Test Log Tail:\n{failure_context.log_tail}\n\n"
            "Analyze the failure log above and apply the necessary fixes.\n"
            f"Ensure the solution satisfies '{requirement}' and resolves the test failures.\n"
        )

    expected_output = (
        "A valid ChangeSet object listing every file changed, action taken (create/modify/delete), "
        "the diff or summary of changes, and an overall summary of the implementation."
    )

    effective_callback = callback or create_crewai_task_callback(run_id=run_id)

    return Task(
        description=description,
        expected_output=expected_output,
        agent=agent,
        output_pydantic=ChangeSet,
        callback=effective_callback,
    )


def run_coding_agent(
    plan: Plan,
    requirement: str,
    tools: list[BaseTool],
    llm: Any,
    failure_context: TestReport | None = None,
    iteration: int = 0,
    max_rpm: int | None = None,
    verbose: bool = False,
    workspace: Path | str | None = None,
    run_id: str | None = None,
) -> ChangeSet:
    """Run the Coding Agent synchronously and return a validated ChangeSet."""
    rpm = max_rpm or int(os.environ.get("REPOAGENT_MAX_RPM", "4"))
    instrumented_tools = instrument_crewai_tools(tools, run_id=run_id, workspace=workspace)
    agent = create_coding_agent(
        llm=llm,
        tools=instrumented_tools,
        max_rpm=rpm,
        verbose=verbose,
        run_id=run_id,
    )
    task = create_coding_task(
        agent=agent,
        plan=plan,
        requirement=requirement,
        failure_context=failure_context,
        iteration=iteration,
        workspace=workspace,
        run_id=run_id,
    )

    import concurrent.futures

    prompt_chars = len(task.description)
    approx_tokens = prompt_chars // 4
    print(
        f"\n[Coding Agent] Iteration {iteration} prompt: ~{approx_tokens} tokens "
        f"({prompt_chars} chars)"
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        max_rpm=rpm,
        verbose=verbose,
        before_kickoff_callbacks=[create_crewai_before_kickoff(run_id=run_id)],
        after_kickoff_callbacks=[create_crewai_after_kickoff(run_id=run_id)],
    )

    timeout_s = int(os.environ.get("REPOAGENT_CODING_TIMEOUT", "600"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(crew.kickoff)
        try:
            future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            err_msg = f"Coding agent timed out after {timeout_s} seconds (Iteration {iteration})."
            print(f"[Coding Agent] {err_msg}")
            with contextlib.suppress(Exception):
                get_event_logger().log_event(
                    run_id=resolve_run_id(run_id),
                    event_type=EventType.ERROR,
                    agent_name="Senior Python Engineer",
                    stage="code",
                    error_details=err_msg,
                )
            raise TimeoutError(f"Coding agent timed out after {timeout_s} seconds.") from None
        except Exception as exc:
            with contextlib.suppress(Exception):
                get_event_logger().log_event(
                    run_id=resolve_run_id(run_id),
                    event_type=EventType.ERROR,
                    agent_name="Senior Python Engineer",
                    stage="code",
                    error_details=f"Crew execution failed: {exc}",
                )
            raise

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
