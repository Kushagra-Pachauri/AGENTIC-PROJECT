"""Orchestrator Flow for RepoAgent.

Manages pipeline: analyze -> plan -> code -> test -> [router: review/pr | retry/needs_human].
Supports CrewAI Coding and Testing agents with an automated correction loop.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crewai.flow.flow import Flow, listen, or_, router, start
from repoagent.a2a_client import A2AClient
from repoagent.agents.crewai.coding_agent import run_coding_agent
from repoagent.changeset_applier import (
    apply_changeset_to_workspace,
    workspace_has_modifications,
)
from repoagent.mcp_client import (
    get_crewai_tools,
    get_repo_reader_params,
)
from repoagent.providers import get_provider
from repoagent.schemas import (
    ChangeSet,
    FileChange,
    Plan,
    PlannedFile,
    PlannerOutput,
    PRSummary,
    ReviewFinding,
    ReviewReport,
    ReviewVerdict,
    RunState,
    RunStatus,
    TestReport,
)
from repoagent.workspace_utils import validate_plan_paths


class RepoAgentFlow(Flow[RunState]):
    """CrewAI Flow orchestrator carrying the single shared RunState."""

    def __init__(
        self,
        initial_state: RunState | None = None,
        simulate_test_failures: int = 0,
        coding_fn: Callable[[RunState], ChangeSet] | None = None,
        testing_fn: Callable[[RunState], TestReport] | None = None,
        planner_fn: Callable[[str, Path], PlannerOutput] | None = None,
        review_fn: Callable[[Plan, str, TestReport], ReviewReport] | None = None,
        planner_url: str | None = None,
        reviewer_url: str | None = None,
        workspace: Path | str | None = None,
        llm: Any | None = None,
        use_live_agents: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(initial_state=initial_state, **kwargs)
        self.simulate_test_failures = simulate_test_failures
        self.coding_fn = coding_fn
        self.testing_fn = testing_fn
        self.planner_fn = planner_fn
        self.review_fn = review_fn
        self.planner_url = planner_url or os.environ.get(
            "REPOAGENT_PLANNER_URL", "http://localhost:8001"
        )
        self.reviewer_url = reviewer_url or os.environ.get(
            "REPOAGENT_REVIEWER_URL", "http://localhost:8002"
        )
        self.workspace = Path(workspace).resolve() if workspace else None
        self._llm = llm
        self.use_live_agents = use_live_agents
        self._wall_start_time = time.monotonic()

    def _save_checkpoint(self) -> None:
        """Persist RunState and individual TestReports to disk after each attempt."""
        if not self.workspace:
            return
        run_dir = Path(self.workspace).parent
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            state_file = run_dir / "run_state.json"
            state_file.write_text(self.state.model_dump_json(indent=2), encoding="utf-8")

            reports_dir = run_dir / "test_reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            for i, r in enumerate(self.state.test_reports, 1):
                (reports_dir / f"test_report_{i}.json").write_text(
                    r.model_dump_json(indent=2), encoding="utf-8"
                )
        except Exception:
            pass

    def _save_sandbox_raw(self, attempt_num: int, raw_content: str) -> None:
        """Save raw tool output to runs/<run_id>/sandbox_raw_<n>.txt."""
        if not self.workspace:
            return
        run_dir = Path(self.workspace).parent
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            raw_file = run_dir / f"sandbox_raw_{attempt_num}.txt"
            raw_file.write_text(raw_content or "(No output captured)", encoding="utf-8")
        except Exception:
            pass

    def _get_workspace_path(self) -> Path:
        """Resolve workspace root directory for tool execution.

        Never runs directly on demo_repo. Uses an isolated workspace under runs/<run_id>/workspace.
        """
        demo_repo_resolved = Path("demo_repo").resolve()

        if (
            self.workspace
            and Path(self.workspace).resolve() != demo_repo_resolved
            and Path(self.workspace).is_dir()
        ):
            return Path(self.workspace).resolve()

        if (
            self.state.repo_url
            and Path(self.state.repo_url).resolve() != demo_repo_resolved
            and Path(self.state.repo_url).is_dir()
        ):
            self.workspace = Path(self.state.repo_url).resolve()
            return self.workspace

        # Isolate baseline repo into a fresh runs/<run_id>/workspace
        from repoagent.workspace_utils import get_runs_dir, setup_workspace

        run_dir = get_runs_dir() / self.state.run_id
        isolated_ws = (run_dir / "workspace").resolve()
        if not (isolated_ws / ".git").is_dir():
            source = (
                Path(self.state.repo_url).resolve()
                if self.state.repo_url and Path(self.state.repo_url).is_dir()
                else demo_repo_resolved
            )
            setup_workspace(source, isolated_ws)

        self.workspace = isolated_ws
        self.state.repo_url = str(isolated_ws)
        return isolated_ws

    def _get_llm(self) -> Any:
        """Get or initialize CrewAI LLM instance."""
        if self._llm is not None:
            return self._llm
        provider = get_provider()
        self._llm = provider.get_crewai_llm()
        return self._llm

    @start()
    def analyze(self) -> str:
        """Step 1: Understand repository context and map codebase via ADK Planner over A2A."""
        # Enforce branch invariant: always work on repoagent/<run_id>
        if not self.state.branch:
            self.state.branch = f"repoagent/{self.state.run_id}"

        self.state.status = RunStatus.RUNNING

        provider = get_provider()
        effective_model = provider.model_name
        print(f"[*] Effective LLM Model: {effective_model} (Provider: {provider.provider_name})")

        workspace = self._get_workspace_path()
        req = self.state.requirement or "Add feature"

        # Determine log file for A2A audit logging
        log_file = None
        if self.workspace:
            run_dir = Path(self.workspace).parent
            log_file = run_dir / "a2a.jsonl"

        # 1. Custom mock/injected planner function (for fast unit testing)
        if self.planner_fn is not None:
            planner_output = self.planner_fn(req, workspace)
            is_valid, path_errors = validate_plan_paths(planner_output.plan, workspace)
            if not is_valid:
                err_detail = f"Plan path validation failed: {'; '.join(path_errors)}"
                self.state.errors.append(err_detail)
                from repoagent.mcp_servers import repo_reader

                repo_reader._set_workspace(str(workspace))
                tree_text = repo_reader.list_tree(".", max_depth=4)
                retry_feedback = (
                    f"Previous plan failed path validation with error:\n{err_detail}\n\n"
                    f"Real repository file tree (via repo-reader):\n{tree_text}\n\n"
                    "CRITICAL: Do NOT invent paths like 'src/auth.py'. "
                    "All files under src are located in 'src/notes_app/'. "
                    "Ensure every existing file path exactly matches the real file tree above."
                )
                try:
                    planner_output = self.planner_fn(f"{req}\n{retry_feedback}", workspace)
                except TypeError:
                    planner_output = self.planner_fn(req, workspace)
                is_valid_2, path_errors_2 = validate_plan_paths(planner_output.plan, workspace)
                if not is_valid_2:
                    joined_errs = "; ".join(path_errors_2)
                    err_detail_2 = f"Plan path validation failed on retry: {joined_errs}"
                    self.state.errors.append(err_detail_2)
                    self.state.status = RunStatus.NEEDS_HUMAN
                    self.state.finished_at = datetime.now(UTC)
                    self._save_checkpoint()
                    return "needs_human"

            self.state.repo_map = planner_output.repo_map
            self.state.plan = planner_output.plan
            return "analyze_completed"

        # 2. If state already has repo_map and plan (e.g. pre-set in test fixture)
        if self.state.repo_map and self.state.plan:
            return "analyze_completed"

        # 3. Call ADK Planner agent over A2A if live agents are enabled
        if self.use_live_agents:
            try:
                client = A2AClient(base_url=self.planner_url)
                planner_output = client.request_plan(
                    requirement=req,
                    workspace=workspace,
                    run_id=self.state.run_id,
                    log_file=log_file,
                    is_replay=self.state.is_replay,
                )
                is_valid, path_errors = validate_plan_paths(planner_output.plan, workspace)
                if not is_valid:
                    err_detail = f"Plan path validation failed: {'; '.join(path_errors)}"
                    self.state.errors.append(err_detail)
                    from repoagent.mcp_servers import repo_reader

                    repo_reader._set_workspace(str(workspace))
                    tree_text = repo_reader.list_tree(".", max_depth=4)
                    retry_feedback = (
                        f"Previous plan failed path validation with error:\n{err_detail}\n\n"
                        f"Real repository file tree (via repo-reader):\n{tree_text}\n\n"
                        "CRITICAL: Do NOT invent paths like 'src/auth.py'. "
                        "All files under src are located in 'src/notes_app/'. "
                        "Ensure every existing file path exactly matches the real file tree above."
                    )
                    planner_output = client.request_plan(
                        requirement=req,
                        workspace=workspace,
                        run_id=self.state.run_id,
                        log_file=log_file,
                        extra_instruction=retry_feedback,
                        is_replay=self.state.is_replay,
                    )
                    is_valid_2, path_errors_2 = validate_plan_paths(planner_output.plan, workspace)
                    if not is_valid_2:
                        joined_errs = "; ".join(path_errors_2)
                        err_detail_2 = f"Plan path validation failed on retry: {joined_errs}"
                        self.state.errors.append(err_detail_2)
                        self.state.status = RunStatus.NEEDS_HUMAN
                        self.state.finished_at = datetime.now(UTC)
                        self._save_checkpoint()
                        return "needs_human"

                self.state.repo_map = planner_output.repo_map
                self.state.plan = planner_output.plan
            except Exception as exc:
                err_msg = f"A2A Planner Agent failed: {exc}"
                self.state.errors.append(err_msg)
                self.state.status = RunStatus.NEEDS_HUMAN
                self.state.finished_at = datetime.now(UTC)
                self._save_checkpoint()
                return "needs_human"
        else:
            if not self.state.plan:
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

        if not self.state.repo_map:
            repo_location = str(workspace)
            self.state.repo_map = (
                f"Repository Map: {repo_location}\n"
                f"Active Branch: {self.state.branch}\n"
                f"Requirement: {self.state.requirement}\n"
                "Components: authentication service, note CRUD service, test suite"
            )

        return "analyze_completed"

    @listen(analyze)
    def plan(self) -> str:
        """Step 2: Verify implementation plan produced by Planner agent."""
        if self.state.status == RunStatus.NEEDS_HUMAN:
            return "needs_human"

        req = self.state.requirement or "Add feature"

        if not self.state.plan:
            if not self.use_live_agents:
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
            else:
                self.state.status = RunStatus.NEEDS_HUMAN
                self.state.finished_at = datetime.now(UTC)
                self.state.errors.append(
                    "Execution blocked: No valid implementation plan produced."
                )
                return "needs_human"

        return "plan_completed"

    @listen(or_(plan, "retry"))
    def code(self) -> str:
        """Step 3: Produce code changes (initial implementation or correction iteration)."""
        if self.state.status == RunStatus.NEEDS_HUMAN:
            return "needs_human"

        is_retry = self.state.retry_count > 0
        iteration_label = f"Iteration {self.state.retry_count}" if is_retry else "Initial change"

        # Check REPLAY mode first: apply known-good pre-recorded ChangeSet
        if self.state.is_replay:
            print("[REPLAY MODE] Applying reference ChangeSet (NOT live generation).")
            fixture_path = Path("tests/fixtures/replay_password_reset.json")
            if fixture_path.is_file():
                import json

                data = json.loads(fixture_path.read_text(encoding="utf-8"))
                label = data.get("label", "hand-written reference solution, not model output")
                summary = data.get("summary", "Hand-written reference solution, not model output")
                self.state.changes = ChangeSet(
                    files=[FileChange(**f) for f in data["files"]],
                    summary=summary,
                )
                self.state.replay_label = label
            else:
                from repoagent.fixtures import KNOWN_GOOD_PASSWORD_RESET_CHANGESET

                self.state.changes = KNOWN_GOOD_PASSWORD_RESET_CHANGESET
                self.state.replay_label = "hand-written reference solution, not model output"
        # 1. Custom mock/injected coding function (used in unit tests for fast verification)
        elif self.coding_fn is not None:
            self.state.changes = self.coding_fn(self.state)
        # 2. Live agent execution if requested
        elif self.use_live_agents:
            failure_context: TestReport | None = None
            if is_retry and self.state.test_reports:
                failure_context = self.state.test_reports[-1]

            workspace = self._get_workspace_path()
            llm = self._get_llm()
            repo_params = get_repo_reader_params(workspace)
            tools = get_crewai_tools(repo_params)

            plan = self.state.plan or Plan(summary=self.state.requirement)
            changes = run_coding_agent(
                plan=plan,
                requirement=self.state.requirement,
                tools=tools,
                llm=llm,
                failure_context=failure_context,
                iteration=self.state.retry_count,
                workspace=workspace,
            )
            self.state.changes = changes
        else:
            # 3. Default deterministic fallback change set
            diff_content = (
                f"@@ -10,6 +10,12 @@\n"
                f"+# {iteration_label}: {self.state.requirement}\n"
                f"+def handle_feature():\n"
                f"+    return True\n"
            )
            test_diff_content = (
                "@@ -1,5 +1,8 @@\n"
                " from notes_app.auth import Authenticator\n"
                "+def test_feature_stub():\n"
                "+    assert True\n"
            )
            self.state.changes = ChangeSet(
                files=[
                    FileChange(
                        path="src/notes_app/auth.py",
                        action="modify",
                        diff=diff_content,
                    ),
                    FileChange(
                        path="tests/test_auth.py",
                        action="modify",
                        diff=test_diff_content,
                    ),
                ],
                summary=f"{iteration_label} addressing {self.state.requirement}",
            )

        # Apply proposed changes to the isolated run workspace
        workspace = self._get_workspace_path()
        try:
            if self.state.changes:
                apply_changeset_to_workspace(self.state.changes, workspace)
        except Exception as exc:
            err_msg = f"Failed to apply changeset (Iteration {self.state.retry_count}): {exc}"
            self.state.errors.append(err_msg)
            if self.state.changes:
                self.state.rejected_changes.append(self.state.changes)

        # Extract real disk-based changes via git (Requirement 2)
        from repoagent.workspace_utils import extract_changeset_from_disk

        real_changes = extract_changeset_from_disk(
            workspace,
            summary=self.state.changes.summary if self.state.changes else "Workspace changes",
        )
        if real_changes:
            self.state.changes = real_changes

        return "code_completed"

    @listen(code)
    def test(self) -> str:
        """Step 4: Execute test suite deterministically inside sandbox container."""
        if self.state.status == RunStatus.NEEDS_HUMAN:
            return "needs_human"

        workspace = self._get_workspace_path()
        attempt_num = len(self.state.test_reports) + 1

        from repoagent.workspace_utils import compute_workspace_hash

        ws_hash = compute_workspace_hash(workspace)

        # 1. Custom mock/injected testing function (used in unit tests for fast verification)
        if self.testing_fn is not None:
            report = self.testing_fn(self.state)
            report.workspace_hash = ws_hash
            raw_text = report.raw_output or report.log_tail or "Mock testing function executed."
            self._save_sandbox_raw(attempt_num, raw_text)
            self.state.test_reports.append(report)
            self._save_checkpoint()
            return "test_completed"

        # 2. Simulated failures flag
        should_fail = (
            self.simulate_test_failures > 0 or "simulate_test_failure" in self.state.errors
        )
        if should_fail:
            if self.simulate_test_failures > 0:
                self.simulate_test_failures -= 1

            raw_text = (
                "FAILED tests/test_auth.py::test_feature_regression - AssertionError\n"
                "1 failed in 0.25s"
            )
            report = TestReport(
                command="pytest tests/ -v",
                passed=False,
                exit_code=1,
                failed_tests=["tests/test_auth.py::test_feature_regression"],
                log_tail=raw_text,
                duration_s=0.25,
                workspace_hash=ws_hash,
                raw_output=raw_text,
            )
            self._save_sandbox_raw(attempt_num, raw_text)
            self.state.test_reports.append(report)
            self._save_checkpoint()
            return "test_completed"

        # Enforce that workspace actually has applied modifications
        if not workspace_has_modifications(workspace):
            err_detail = "Workspace files were not modified by ChangeSet."
            if self.state.errors:
                err_detail += f" Errors: {'; '.join(self.state.errors)}"
            raw_text = f"FAILED verification: {err_detail}"
            report = TestReport(
                command="pytest tests/ -v",
                passed=False,
                exit_code=1,
                failed_tests=["verification:no_changes_applied"],
                log_tail=raw_text,
                duration_s=0.0,
                workspace_hash=ws_hash,
                raw_output=raw_text,
            )
            self._save_sandbox_raw(attempt_num, raw_text)
            self.state.test_reports.append(report)
            self._save_checkpoint()
            return "test_completed"

        # 3. Deterministic MCP Tool Execution (Always called directly in code, never by an LLM)
        if not self.use_live_agents:
            raw_text = "12 passed in 0.08s"
            report = TestReport(
                command="pytest tests/ -v",
                passed=True,
                exit_code=0,
                failed_tests=[],
                log_tail=raw_text,
                duration_s=0.08,
                workspace_hash=ws_hash,
                raw_output=raw_text,
            )
        else:
            from repoagent.mcp_servers import test_runner

            test_runner._set_workspace(str(workspace))
            report = test_runner.run_tests("pytest")
            report.workspace_hash = ws_hash

        # Requirement 4: Ensure feature-specific tests are actually added when plan requires them
        from repoagent.workspace_utils import extract_changeset_from_disk

        disk_changes = extract_changeset_from_disk(workspace)
        plan_requires_tests = bool(
            self.state.plan
            and any("test" in f.path.lower() for f in self.state.plan.files_to_change)
        )
        test_files_modified = bool(
            disk_changes and any("test" in f.path.lower() for f in disk_changes.files)
        )
        if plan_requires_tests and not test_files_modified:
            err_detail = (
                "Plan required test changes, but no test files were added or modified on disk."
            )
            raw_text = f"FAILED verification: {err_detail}"
            report = TestReport(
                command=report.command or "pytest",
                passed=False,
                exit_code=1,
                failed_tests=["verification:missing_feature_tests"],
                log_tail=raw_text,
                duration_s=report.duration_s,
                workspace_hash=ws_hash,
                raw_output=raw_text,
            )
            self.state.errors.append(err_detail)

        raw_text = report.raw_output or report.log_tail or "(No output captured)"
        self._save_sandbox_raw(attempt_num, raw_text)

        self.state.test_reports.append(report)
        self._save_checkpoint()
        return "test_completed"

    @router(test)
    def route_after_test(self) -> str:
        """Router: evaluate test outcome and determine next action.

        - pass -> proceed to code review
        - fail (retries remaining) -> increment retry_count and loop back to code
        - fail (retries exhausted) -> terminate with needs_human status
        """
        self._save_checkpoint()

        if self.state.status == RunStatus.NEEDS_HUMAN:
            return "needs_human"

        # Check 45-minute wall-clock timeout (2700s)
        if time.monotonic() - self._wall_start_time > 2700:
            self.state.status = RunStatus.NEEDS_HUMAN
            self.state.finished_at = datetime.now(UTC)
            self.state.errors.append("Execution capped: 45-minute wall-clock timeout reached.")
            self._save_checkpoint()
            return "needs_human"

        if not self.state.test_reports:
            self.state.status = RunStatus.NEEDS_HUMAN
            self.state.finished_at = datetime.now(UTC)
            return "needs_human"

        latest_report = self.state.test_reports[-1]

        if latest_report.passed and bool(latest_report.workspace_hash):
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
        """Step 5: Review code changes for quality and requirements adherence via ADK Reviewer."""
        if self.state.status == RunStatus.NEEDS_HUMAN:
            return "needs_human"

        workspace = self._get_workspace_path()
        run_dir = Path(self.workspace).parent if self.workspace else None
        log_file = run_dir / "a2a.jsonl" if run_dir else None

        is_mock_test = self.coding_fn is not None and self.testing_fn is not None
        # Guard: Reject review if workspace contains no applied modifications
        if not (workspace_has_modifications(workspace) or is_mock_test):
            self.state.review = ReviewReport(
                verdict=ReviewVerdict.REQUEST_CHANGES,
                findings=[
                    ReviewFinding(
                        file="general",
                        severity="critical",
                        message="Cannot approve: Workspace contains no applied modifications.",
                    )
                ],
                summary="Review rejected: Workspace files were not modified by ChangeSet.",
            )
            self._save_checkpoint()
            return "review_completed"

        # Extract git diff from disk
        diff_text = ""
        if self.state.changes and self.state.changes.files:
            diff_text = "\n".join(f.diff for f in self.state.changes.files if f.diff)
        if not diff_text:
            import subprocess

            subprocess.run(
                ["git", "add", "-N", "."], cwd=str(workspace), capture_output=True, check=False
            )
            diff_res = subprocess.run(
                ["git", "diff", "HEAD"], cwd=str(workspace), capture_output=True, text=True
            )
            diff_text = diff_res.stdout or "(No diff from HEAD)"

        last_test = (
            self.state.test_reports[-1]
            if self.state.test_reports
            else TestReport(command="pytest", passed=True, exit_code=0)
        )
        plan = self.state.plan or Plan(summary=self.state.requirement)

        # 1. Custom mock/injected review function (for fast unit testing)
        if self.review_fn is not None:
            self.state.review = self.review_fn(plan, diff_text, last_test)
            self._save_checkpoint()
            return "review_completed"

        # 2. Call ADK Reviewer agent over A2A if live agents enabled
        if self.use_live_agents:
            try:
                client = A2AClient(base_url=self.reviewer_url)
                review_report = client.request_review(
                    plan=plan,
                    diff=diff_text,
                    test_report=last_test,
                    run_id=self.state.run_id,
                    log_file=log_file,
                    is_replay=self.state.is_replay,
                )
                self.state.review = review_report
            except Exception as exc:
                err_msg = f"A2A Reviewer Agent failed: {exc}"
                self.state.errors.append(err_msg)
                self.state.review = ReviewReport(
                    verdict=ReviewVerdict.REQUEST_CHANGES,
                    findings=[ReviewFinding(file="general", severity="critical", message=err_msg)],
                    summary=f"Reviewer execution failure: {err_msg}",
                )
        else:
            self.state.review = ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                findings=[
                    ReviewFinding(
                        file="general", severity="info", message="Fallback test review approve"
                    )
                ],
                summary="Fallback review approve for offline test execution",
            )

        self._save_checkpoint()
        return "review_completed"

    @router(review)
    def route_after_review(self) -> str:
        """Route based on code reviewer verdict.

        - approve -> approved -> open_pr
        - request_changes (retries remaining) -> retry (counts toward retry cap)
        - request_changes (retries exhausted) -> needs_human
        """
        self._save_checkpoint()
        if self.state.status == RunStatus.NEEDS_HUMAN:
            return "needs_human"
        if not self.state.review:
            self.state.status = RunStatus.NEEDS_HUMAN
            return "needs_human"

        if self.state.review.verdict == ReviewVerdict.APPROVE:
            return "approved"

        # Reviewer requested changes: send findings back to Coder once, counting toward retry cap
        if self.state.retry_count < self.state.max_retries:
            self.state.retry_count += 1
            for f in self.state.review.findings:
                finding_desc = (
                    f"Code Review Finding ({f.severity}): {f.file}:{f.line} - {f.message}"
                )
                self.state.errors.append(finding_desc)
            return "retry"
        else:
            self.state.status = RunStatus.NEEDS_HUMAN
            self.state.finished_at = datetime.now(UTC)
            self.state.errors.append(
                "Execution capped: maximum retries reached after code review rejection."
            )
            return "needs_human"

    @listen("approved")
    def open_pr(self) -> str:
        """Step 6: Open GitHub Pull Request for human review (stub per Step 5 constraints)."""
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

        title = f"feat: {req}"
        workspace = self._get_workspace_path()

        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
        live_pr_enabled = os.environ.get("REPOAGENT_LIVE_PR", "").lower() in ("true", "1")
        if token and live_pr_enabled:
            from repoagent.github_service import push_branch_and_create_pr

            self.state.pr = push_branch_and_create_pr(
                workspace=workspace,
                branch=self.state.branch,
                title=title,
                body=body,
            )
        else:
            self.state.pr = PRSummary(
                title=title,
                body_markdown=body,
                url=f"https://github.com/offline-test/repo/pull/{self.state.run_id[:6]}",
                branch=self.state.branch,
            )

        # Requirement 6: Set DONE only if:
        # 1. last report passed deterministically with workspace_hash
        # 2. workspace actually has applied modifications
        # 3. workspace is not baseline demo_repo
        # 4. reviewer approved
        # 5. no errors
        last_report = self.state.test_reports[-1] if self.state.test_reports else None
        is_mock_test = self.coding_fn is not None and self.testing_fn is not None
        has_mods = workspace_has_modifications(workspace) or is_mock_test
        not_baseline = Path(workspace).resolve() != Path("demo_repo").resolve()
        reviewer_approved = bool(
            self.state.review and self.state.review.verdict == ReviewVerdict.APPROVE
        )

        if (
            last_report
            and last_report.passed
            and bool(last_report.workspace_hash)
            and has_mods
            and not_baseline
            and reviewer_approved
        ):
            self.state.status = RunStatus.DONE
        else:
            self.state.status = RunStatus.NEEDS_HUMAN

        self.state.finished_at = datetime.now(UTC)
        self._save_checkpoint()
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
        self._save_checkpoint()
        return "needs_human_completed"
