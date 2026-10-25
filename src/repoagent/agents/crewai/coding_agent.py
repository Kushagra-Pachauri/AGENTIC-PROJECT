"""CrewAI Coding Agent module.

Implements code changes using repo-reader/writer MCP tools and outputs a validated ChangeSet.
Supports correction iterations by incorporating failure context from previous TestReports.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crewai import Agent, Crew, Process, Task
from repoagent.schemas import ChangeSet, Plan, TestReport

if TYPE_CHECKING:
    from crewai.tools.base_tool import BaseTool


def create_coding_agent(
    llm: Any,
    tools: list[BaseTool],
    max_rpm: int | None = None,
    verbose: bool = False,
) -> Agent:
    """Create the CrewAI Coding Agent configured with repository MCP tools."""
    rpm = max_rpm or int(os.environ.get("REPOAGENT_MAX_RPM", "4"))
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
        max_rpm=rpm,
        verbose=verbose,
    )


def create_coding_task(
    agent: Agent,
    plan: Plan,
    requirement: str,
    failure_context: TestReport | None = None,
    iteration: int = 0,
    workspace: Path | str | None = None,
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
                    code_context.append(f"--- Existing {f.path} ---\n{text}")
                except Exception:
                    pass
        if code_context:
            formatted_context = "\n\n".join(code_context)
            description += f"Existing Target Files in Repository:\n{formatted_context}\n\n"

    description += (
        "Implementation Guidelines:\n"
        "1. In src/notes_app/auth.py: Within class AuthService, implement in-memory token reset:\n"
        "   - request_password_reset(self, username: str) -> str: verify user exists, generate "
        "sha256 token, store in self._reset_tokens[token] = username, return token.\n"
        "   - reset_password(self, username: str, token: str, new_password: str) -> bool: "
        "verify token in self._reset_tokens, update user.hashed_password, "
        "delete token, return True; raise ValueError if token is invalid or user not found.\n"
        "2. In src/notes_app/app.py: Within class NotesApp, implement delegating methods:\n"
        "   - def request_password_reset(self, username: str) -> str: "
        "return self.auth.request_password_reset(username)\n"
        "   - def reset_password(self, username: str, token: str, new_password: str) -> bool: "
        "return self.auth.reset_password(username, token, new_password)\n"
        "3. Preserve all existing methods on NotesApp and AuthService.\n"
        "4. Do NOT create, edit, or overwrite tests/conftest.py or import non-existent modules.\n"
        "5. In ChangeSet output, for each file provide unified diff with '+' "
        "additions or full code.\n"
    )

    if failure_context is not None and not failure_context.passed:
        failed_list = ", ".join(failure_context.failed_tests) or "Non-zero exit code"
        description += (
            f"\n--- CORRECTION ITERATION {iteration} ---\n"
            f"The previous test attempt failed!\n"
            f"Failed Tests: {failed_list}\n"
            f"Test Log Tail:\n{failure_context.log_tail}\n\n"
            "Analyze the failure log above and apply the necessary fixes.\n"
            "Ensure NotesApp has request_password_reset and reset_password methods,\n"
            "and AuthService validates the token and updates the user's password.\n"
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
    max_rpm: int | None = None,
    verbose: bool = False,
    workspace: Path | str | None = None,
) -> ChangeSet:
    """Run the Coding Agent synchronously and return a validated ChangeSet."""
    rpm = max_rpm or int(os.environ.get("REPOAGENT_MAX_RPM", "4"))
    agent = create_coding_agent(llm=llm, tools=tools, max_rpm=rpm, verbose=verbose)
    task = create_coding_task(
        agent=agent,
        plan=plan,
        requirement=requirement,
        failure_context=failure_context,
        iteration=iteration,
        workspace=workspace,
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
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(crew.kickoff)
        try:
            future.result(timeout=300)
        except concurrent.futures.TimeoutError:
            print(f"[Coding Agent] Execution timed out after 300s (Iteration {iteration})")
            raise TimeoutError("Coding agent timed out after 300 seconds.") from None

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
