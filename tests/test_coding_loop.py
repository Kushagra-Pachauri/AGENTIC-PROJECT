"""Unit and integration tests for CrewAI Coding and Testing agents correction loop.

Verifies:
  - Task output validation as Pydantic models (ChangeSet and TestReport)
  - Failure context propagation to Coding Agent on retry
  - Mocked-LLM test for loop logic:
    - Sequence 1: fail -> fail -> pass (success after 2 retries)
    - Sequence 2: fail three times (reaches retry cap and stops with needs_human)
  - Full tracking of every attempt in RunState.test_reports
"""

from __future__ import annotations

from repoagent.agents.crewai.coding_agent import (
    create_coding_agent,
    create_coding_task,
)
from repoagent.agents.crewai.testing_agent import (
    create_testing_agent,
    create_testing_task,
)
from repoagent.fixtures import PASSWORD_RESET_PLAN
from repoagent.flow import RepoAgentFlow
from repoagent.schemas import (
    ChangeSet,
    FileChange,
    RunState,
    RunStatus,
    TestReport,
)


class TestAgentPydanticOutput:
    """Verify task output configuration on Coding and Testing agents."""

    def test_coding_task_output_pydantic(self) -> None:
        agent = create_coding_agent(llm="gpt-4o", tools=[])
        task = create_coding_task(
            agent=agent,
            plan=PASSWORD_RESET_PLAN,
            requirement="Add password-reset functionality",
        )
        assert task.output_pydantic == ChangeSet
        assert "ChangeSet" in task.expected_output

    def test_testing_task_output_pydantic(self) -> None:
        agent = create_testing_agent(llm="gpt-4o", tools=[])
        task = create_testing_task(agent=agent, command="pytest")
        assert task.output_pydantic == TestReport
        assert "TestReport" in task.expected_output

    def test_failure_context_passed_to_coding_task(self) -> None:
        """Verify failed_tests and log_tail from previous attempt appear in task description."""
        agent = create_coding_agent(llm="gpt-4o", tools=[])
        failure = TestReport(
            command="pytest",
            passed=False,
            exit_code=1,
            failed_tests=["tests/test_auth.py::test_reset_password"],
            log_tail="AssertionError: password reset token invalid",
            duration_s=0.5,
        )
        task = create_coding_task(
            agent=agent,
            plan=PASSWORD_RESET_PLAN,
            requirement="Add password-reset functionality",
            failure_context=failure,
            iteration=1,
        )
        assert "CORRECTION ITERATION 1" in task.description
        assert "tests/test_auth.py::test_reset_password" in task.description
        assert "AssertionError: password reset token invalid" in task.description


class TestCorrectionLoopLogic:
    """Verify the orchestrator loop logic with mocked LLM/testing callbacks."""

    def test_loop_fail_fail_pass(self) -> None:
        """Simulate a test sequence: fail -> fail -> pass.

        Verifies that:
          1. The Coding Agent receives context from each failed attempt.
          2. The loop runs exactly 2 retries (3 attempts total).
          3. Every attempt is preserved in RunState.test_reports.
          4. Final status is DONE.
        """
        received_contexts: list[str] = []

        # Simulated test outcomes: attempt 0 fails, attempt 1 fails, attempt 2 passes
        test_outcomes = [
            TestReport(
                command="pytest",
                passed=False,
                exit_code=1,
                failed_tests=["tests/test_auth.py::test_reset_password_unimplemented"],
                log_tail="FAILED test_reset_password_unimplemented - AttributeError",
                duration_s=0.2,
            ),
            TestReport(
                command="pytest",
                passed=False,
                exit_code=1,
                failed_tests=["tests/test_auth.py::test_reset_password_token_expired"],
                log_tail="FAILED test_reset_password_token_expired - TokenExpiredError",
                duration_s=0.3,
            ),
            TestReport(
                command="pytest",
                passed=True,
                exit_code=0,
                failed_tests=[],
                log_tail="14 passed in 0.35s",
                duration_s=0.35,
            ),
        ]

        attempt = 0

        def mock_testing(state: RunState) -> TestReport:
            nonlocal attempt
            report = test_outcomes[attempt]
            attempt += 1
            return report

        def mock_coding(state: RunState) -> ChangeSet:
            # Capture failure context passed to coding iteration
            if state.retry_count > 0 and state.test_reports:
                last_report = state.test_reports[-1]
                received_contexts.append(
                    f"Retry {state.retry_count}: {last_report.failed_tests[0]}"
                )
            return ChangeSet(
                files=[
                    FileChange(
                        path="src/notes_app/auth.py",
                        action="modify",
                        diff=f"+# Iteration {state.retry_count} fix",
                    )
                ],
                summary=f"Fix for iteration {state.retry_count}",
            )

        initial_state = RunState(
            requirement="Add password-reset functionality",
            repo_url="demo_repo",
            max_retries=3,
        )

        flow = RepoAgentFlow(
            initial_state=initial_state,
            coding_fn=mock_coding,
            testing_fn=mock_testing,
        )

        result = flow.kickoff()

        # Outcome verification
        assert result == "open_pr_completed"
        assert flow.state.status == RunStatus.DONE
        assert flow.state.retry_count == 2

        # Verify every attempt is preserved in test_reports
        assert len(flow.state.test_reports) == 3
        assert flow.state.test_reports[0].passed is False
        assert flow.state.test_reports[1].passed is False
        assert flow.state.test_reports[2].passed is True

        # Verify failure context was delivered to the coding step on each retry
        assert len(received_contexts) == 2
        assert "test_reset_password_unimplemented" in received_contexts[0]
        assert "test_reset_password_token_expired" in received_contexts[1]

        # Verify PR was generated
        assert flow.state.pr is not None
        assert "2 / 3" in flow.state.pr.body_markdown

    def test_loop_fail_three_times_needs_human(self) -> None:
        """Simulate persistent test failures exhausting max_retries (3).

        Verifies that:
          1. The loop terminates with status NEEDS_HUMAN.
          2. Exactly 4 test reports (initial + 3 retries) are recorded.
          3. No PR is opened.
          4. Explanatory error is logged in RunState.errors.
        """
        attempt = 0

        def mock_failing_test(state: RunState) -> TestReport:
            nonlocal attempt
            attempt += 1
            return TestReport(
                command="pytest",
                passed=False,
                exit_code=1,
                failed_tests=[f"tests/test_auth.py::failing_case_{attempt}"],
                log_tail=f"FAILED case {attempt} - UnresolvedBug",
                duration_s=0.1,
            )

        initial_state = RunState(
            requirement="Add complex auth token with bugs",
            repo_url="demo_repo",
            max_retries=3,
        )

        flow = RepoAgentFlow(
            initial_state=initial_state,
            testing_fn=mock_failing_test,
        )

        result = flow.kickoff()

        assert result == "needs_human_completed"
        assert flow.state.status == RunStatus.NEEDS_HUMAN
        assert flow.state.retry_count == 3
        assert flow.state.pr is None
        assert flow.state.review is None

        # Verify all 4 attempts (1 initial + 3 retries) are recorded
        assert len(flow.state.test_reports) == 4
        for r in flow.state.test_reports:
            assert r.passed is False

        # Verify error message logged
        assert any(
            "Execution capped after 3 correction iterations" in err for err in flow.state.errors
        )
