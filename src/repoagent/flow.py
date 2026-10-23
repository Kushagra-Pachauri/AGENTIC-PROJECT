"""Orchestrator Flow for RepoAgent.

Manages pipeline: analyze -> plan -> code -> test -> [router: review/pr | retry/needs_human].
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from crewai.flow.flow import Flow, listen, or_, router, start

from repoagent.schemas import (
    ChangeSet,
    FileChange,
    Plan,
    PlannedFile,
    PRSummary,
    ReviewFinding,
    ReviewReport,
    ReviewVerdict,
    RunState,
    RunStatus,
    TestReport,
)


class RepoAgentFlow(Flow[RunState]):
    """CrewAI Flow orchestrator carrying the single shared RunState."""

    def __init__(
        self,
        initial_state: RunState | None = None,
        simulate_test_failures: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(initial_state=initial_state, **kwargs)
        self.simulate_test_failures = simulate_test_failures

    @start()
    def analyze(self) -> str:
        """Step 1: Understand repository context and map codebase."""
        # Enforce branch invariant: always work on repoagent/<run_id>
        if not self.state.branch:
            self.state.branch = f"repoagent/{self.state.run_id}"

        self.state.status = RunStatus.RUNNING

        # Build repository map stub
        repo_location = self.state.repo_url or "local:demo_repo"
        self.state.repo_map = (
            f"Repository Map: {repo_location}\n"
            f"Active Branch: {self.state.branch}\n"
            f"Requirement: {self.state.requirement}\n"
            "Components: authentication service, note CRUD service, test suite"
        )
        return "analyze_completed"

    @listen(analyze)
    def plan(self) -> str:
        """Step 2: Generate implementation plan based on repository map and requirement."""
        req = self.state.requirement or "Feature implementation"
        self.state.plan = Plan(
            summary=f"Plan: {req}",
            files_to_change=[
                PlannedFile(
                    path="src/notes_app/auth.py",
                    reason="Implement requested functionality",
                ),
                PlannedFile(path="tests/test_auth.py", reason="Add regression tests"),
            ],
            steps=[
                f"1. Analyze target interfaces for '{req}'",
                "2. Implement logic with strict input validation",
                "3. Add unit tests and verify in sandbox",
            ],
            risks=["Potential security flaws if tokens are predictable or unexpired"],
            test_strategy="Execute test suite in isolated Docker container",
        )
        return "plan_completed"

    @listen(or_(plan, "retry"))
    def code(self) -> str:
        """Step 3: Produce code changes (initial implementation or correction iteration)."""
        is_retry = self.state.retry_count > 0
        iteration_label = f"Iteration {self.state.retry_count}" if is_retry else "Initial change"

        diff_content = (
            f"@@ -10,6 +10,12 @@\n"
            f"+# {iteration_label}: {self.state.requirement}\n"
            f"+def handle_feature():\n"
            f"+    return True\n"
        )

        self.state.changes = ChangeSet(
            files=[
                FileChange(
                    path="src/notes_app/auth.py",
                    action="modify",
                    diff=diff_content,
                )
            ],
            summary=f"{iteration_label} addressing {self.state.requirement}",
        )
        return "code_completed"

    @listen(code)
    def test(self) -> str:
        """Step 4: Execute test suite inside sandbox container."""
        # Determine whether to simulate failure for testing retry cap
        should_fail = (
            self.simulate_test_failures > 0 or "simulate_test_failure" in self.state.errors
        )

        if should_fail:
            # If simulated, decrements counter if greater than 0
            if self.simulate_test_failures > 0:
                self.simulate_test_failures -= 1

            report = TestReport(
                command="pytest tests/ -v",
                passed=False,
                exit_code=1,
                failed_tests=["tests/test_auth.py::test_feature_regression"],
                log_tail="FAILED tests/test_auth.py::test_feature_regression - AssertionError",
                duration_s=0.25,
            )
        else:
            report = TestReport(
                command="pytest tests/ -v",
                passed=True,
                exit_code=0,
                failed_tests=[],
                log_tail="12 passed in 0.08s",
                duration_s=0.08,
            )

        self.state.test_reports.append(report)
        return "test_completed"

    @router(test)
    def route_after_test(self) -> str:
        """Router: evaluate test outcome and determine next action.

        - pass -> proceed to code review
        - fail (retries remaining) -> increment retry_count and loop back to code
        - fail (retries exhausted) -> terminate with needs_human status
        """
        if not self.state.test_reports:
            return "pass"

        latest_report = self.state.test_reports[-1]

        if latest_report.passed:
            return "pass"

        # Test failed: check retry budget
        if self.state.retry_count < self.state.max_retries:
            self.state.retry_count += 1
            return "retry"
        else:
            self.state.status = RunStatus.NEEDS_HUMAN
            self.state.finished_at = datetime.now(UTC)
            return "needs_human"

    @listen("pass")
    def review(self) -> str:
        """Step 5: Review code changes for quality, security, and requirement adherence."""
        self.state.review = ReviewReport(
            verdict=ReviewVerdict.APPROVE,
            findings=[
                ReviewFinding(
                    severity="info",
                    file="src/notes_app/auth.py",
                    line=10,
                    message="Change complies with architecture; sandbox tests passed.",
                )
            ],
            summary="Approved: code changes passed validation and testing.",
        )
        return "review_completed"

    @listen(review)
    def open_pr(self) -> str:
        """Step 6: Open GitHub Pull Request for human review."""
        req = self.state.requirement or "Feature update"
        plan_summary = self.state.plan.summary if self.state.plan else "N/A"
        review_summary = self.state.review.summary if self.state.review else "N/A"

        body = (
            f"## Summary\n\n"
            f"Automated PR produced by RepoAgent for requirement:\n> {req}\n\n"
            f"### Plan\n{plan_summary}\n\n"
            f"### Review Verdict\n{review_summary}\n\n"
            f"### Retries\n{self.state.retry_count} / {self.state.max_retries}\n"
        )

        self.state.pr = PRSummary(
            title=f"feat: {req}",
            body_markdown=body,
            url=f"https://github.com/example/repo/pull/{self.state.run_id[:6]}",
            branch=self.state.branch,
        )
        self.state.status = RunStatus.DONE
        self.state.finished_at = datetime.now(UTC)
        return "open_pr_completed"

    @listen("needs_human")
    def handle_needs_human(self) -> str:
        """Terminal handler when maximum retry limit is reached."""
        self.state.status = RunStatus.NEEDS_HUMAN
        self.state.finished_at = datetime.now(UTC)
        msg = (
            f"Execution capped after {self.state.retry_count} correction iterations. "
            "Requires human intervention."
        )
        if msg not in self.state.errors:
            self.state.errors.append(msg)
        return "needs_human_completed"
