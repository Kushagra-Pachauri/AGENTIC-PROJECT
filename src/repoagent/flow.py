"""Orchestrator Flow for RepoAgent.

Manages pipeline: analyze -> plan -> code -> test -> [router: review/pr | retry/needs_human].
Supports CrewAI Coding and Testing agents with an automated correction loop.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from crewai.flow.flow import Flow, listen, or_, router, start
from repoagent.a2a_client import A2AClient
from repoagent.agents.crewai.coding_agent import run_coding_agent
from repoagent.changeset_applier import (
    apply_changeset_to_workspace,
    workspace_has_modifications,
)
from repoagent.done_gate import evaluate_done_gate
from repoagent.mcp_client import (
    get_crewai_tools,
    get_repo_reader_params,
)
from repoagent.monitoring import EventType, get_event_logger, set_current_run_id
from repoagent.observability import redact_text_for_tracing
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
from repoagent.workspace_utils import get_runs_dir, validate_plan_paths

load_dotenv("/app/.env", override=False)
load_dotenv(override=False)

REGISTERED_REPLAY_BENCHMARKS = {
    "Add a password-reset functionality",
    "Add password-reset functionality",
    "Add password reset",
}


logger = logging.getLogger(__name__)


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
        create_pr: bool = False,
        is_mock_test: bool | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(initial_state=initial_state, **kwargs)
        if self.state.is_replay and self.state.requirement not in REGISTERED_REPLAY_BENCHMARKS:
            msg = (
                f"REPLAY mode rejected: requirement '{self.state.requirement}' is not a registered "
                f"reference replay benchmark. REPLAY mode is strictly reserved for pre-recorded "
                f"reference solutions. For custom features, run in live mode without --replay."
            )
            self.state.status = RunStatus.BLOCKED
            self.state.errors.append(msg)
            raise ValueError(msg)
        self.simulate_test_failures = simulate_test_failures
        self.coding_fn = coding_fn
        self.testing_fn = testing_fn
        self.planner_fn = planner_fn
        self.review_fn = review_fn
        mock_env = os.environ.get("REPOAGENT_MOCK_TEST", "").lower() in ("true", "1")
        self.is_mock_test = (
            is_mock_test
            if is_mock_test is not None
            else bool((self.coding_fn is not None and self.testing_fn is not None) or mock_env)
        )
        self.planner_url = planner_url or os.environ.get(
            "REPOAGENT_PLANNER_URL", "http://localhost:8001"
        )
        self.reviewer_url = reviewer_url or os.environ.get(
            "REPOAGENT_REVIEWER_URL", "http://localhost:8002"
        )
        self.workspace = Path(workspace).resolve() if workspace else None
        self._llm = llm
        self.use_live_agents = use_live_agents
        self.state.is_live_execution = use_live_agents
        self.create_pr = create_pr
        self._wall_start_time = time.monotonic()

    def _save_checkpoint(self) -> bool:
        """Persist RunState and individual TestReports to disk atomically after each attempt.

        Returns True if checkpoint successfully written, False otherwise.
        All serializations happen before writing files.
        All temporary files are written first, then atomically replaced only after all succeed.
        Cleans up temporary files on any failure and logs actionable diagnostic context.
        """
        if not self.workspace:
            return False
        run_dir = Path(self.workspace).parent
        run_id = self.state.run_id if hasattr(self, "state") and self.state else "unknown"
        created_tmp_files: list[Path] = []
        stage = "serialization"
        try:
            # Stage 1: In-memory serialization of state and all test reports
            state_json = self.state.model_dump_json(indent=2)
            report_jsons: list[tuple[int, str]] = [
                (i, r.model_dump_json(indent=2)) for i, r in enumerate(self.state.test_reports, 1)
            ]

            # Stage 2: Write all temporary files
            stage = "temp_write"
            run_dir.mkdir(parents=True, exist_ok=True)
            planned_replacements: list[tuple[Path, Path]] = []

            # Write temporary run_state
            state_file = run_dir / "run_state.json"
            tmp_state = run_dir / f"run_state.tmp.{uuid.uuid4().hex}"
            tmp_state.write_text(state_json, encoding="utf-8")
            created_tmp_files.append(tmp_state)
            planned_replacements.append((tmp_state, state_file))

            # Write temporary test reports
            if report_jsons:
                reports_dir = run_dir / "test_reports"
                reports_dir.mkdir(parents=True, exist_ok=True)
                for i, r_json in report_jsons:
                    target_report = reports_dir / f"test_report_{i}.json"
                    tmp_report = reports_dir / f"test_report_{i}.tmp.{uuid.uuid4().hex}"
                    tmp_report.write_text(r_json, encoding="utf-8")
                    created_tmp_files.append(tmp_report)
                    planned_replacements.append((tmp_report, target_report))

            # Stage 3: Atomic replacements
            stage = "atomic_replace"
            for tmp_path, target_path in planned_replacements:
                os.replace(tmp_path, target_path)
                if tmp_path in created_tmp_files:
                    created_tmp_files.remove(tmp_path)

            return True
        except Exception as exc:
            for tmp_path in list(created_tmp_files):
                try:
                    if tmp_path.exists():
                        tmp_path.unlink(missing_ok=True)
                except Exception as cleanup_exc:
                    logger.warning(
                        "Failed to clean up temporary checkpoint file %s for run %s: %s",
                        tmp_path,
                        run_id,
                        cleanup_exc,
                    )
            sanitized_exc = redact_text_for_tracing(str(exc))
            logger.error(
                "Failed to save checkpoint for run %s during stage '%s': %s",
                run_id,
                stage,
                sanitized_exc,
            )
            return False

    def _save_sandbox_raw(self, attempt_num: int, raw_content: str) -> bool:
        """Save raw tool output to runs/<run_id>/sandbox_raw_<n>.txt atomically."""
        if not self.workspace:
            return False
        run_dir = Path(self.workspace).parent
        run_id = self.state.run_id if hasattr(self, "state") and self.state else "unknown"
        tmp_file: Path | None = None
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            raw_file = run_dir / f"sandbox_raw_{attempt_num}.txt"
            tmp_file = run_dir / f"sandbox_raw_{attempt_num}.tmp.{uuid.uuid4().hex}"
            tmp_file.write_text(raw_content or "(No output captured)", encoding="utf-8")
            os.replace(tmp_file, raw_file)
            return True
        except Exception as exc:
            if tmp_file and tmp_file.exists():
                with contextlib.suppress(Exception):
                    tmp_file.unlink(missing_ok=True)
            sanitized_exc = redact_text_for_tracing(str(exc))
            logger.error(
                "Failed to save raw sandbox output for run %s (attempt %s): %s",
                run_id,
                attempt_num,
                sanitized_exc,
            )
            return False

    def _get_workspace_path(self) -> Path:
        """Resolve workspace root directory for tool execution.

        Never runs directly on demo_repo. Uses an isolated workspace under runs/<run_id>/workspace.
        """
        demo_repo_resolved = Path("demo_repo").resolve()

        from repoagent.monitoring import register_run_workspace, set_current_workspace

        resolved_ws: Path
        if (
            self.workspace
            and Path(self.workspace).resolve() != demo_repo_resolved
            and Path(self.workspace).is_dir()
        ):
            resolved_ws = Path(self.workspace).resolve()
            register_run_workspace(self.state.run_id, resolved_ws)
            set_current_workspace(resolved_ws)
            return resolved_ws

        if (
            self.state.repo_url
            and Path(self.state.repo_url).resolve() != demo_repo_resolved
            and Path(self.state.repo_url).is_dir()
        ):
            self.workspace = Path(self.state.repo_url).resolve()
            register_run_workspace(self.state.run_id, self.workspace)
            set_current_workspace(self.workspace)
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
        register_run_workspace(self.state.run_id, isolated_ws)
        set_current_workspace(isolated_ws)
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
        set_current_run_id(self.state.run_id)
        # Enforce branch invariant: always work on repoagent/<run_id>
        if not self.state.branch:
            self.state.branch = f"repoagent/{self.state.run_id}"

        self.state.status = RunStatus.RUNNING

        from repoagent.monitoring import get_event_logger

        if not getattr(self, "_run_started_logged", False):
            get_event_logger().log_run_started(
                run_id=self.state.run_id,
                requirement=self.state.requirement,
                repo_url=self.state.repo_url,
                metadata={"branch": self.state.branch, "is_replay": self.state.is_replay},
            )
            self._run_started_logged = True

        provider = get_provider()
        effective_model = provider.model_name
        print(f"[*] Effective LLM Model: {effective_model} (Provider: {provider.provider_name})")

        workspace = self._get_workspace_path()
        req = self.state.requirement or "Add feature"

        # Screen user requirement via centralized input guardrails
        from repoagent.security import GuardrailDecision, screen_user_requirement

        screening = screen_user_requirement(self.state.requirement, run_id=self.state.run_id)
        if screening.decision == GuardrailDecision.BLOCK:
            self.state.status = RunStatus.BLOCKED
            self.state.finished_at = datetime.now(UTC)
            err_msg = f"User requirement blocked by input guardrail: {screening.reason}"
            self.state.errors.append(err_msg)
            self._save_checkpoint()
            return "needs_human"
        elif screening.decision == GuardrailDecision.FLAG:
            self.state.status = RunStatus.NEEDS_HUMAN
            self.state.finished_at = datetime.now(UTC)
            err_msg = (
                f"User requirement flagged by input guardrail for human review: {screening.reason}"
            )
            self.state.errors.append(err_msg)
            self._save_checkpoint()
            return "needs_human"

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
                from repoagent.security import InputSource, screen_input

                tree_screen = screen_input(
                    tree_text, source=InputSource.MCP_OUTPUT, run_id=self.state.run_id
                )
                if tree_screen.is_suspicious and tree_screen.sanitized_content:
                    tree_text = tree_screen.sanitized_content

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
                    from repoagent.security import InputSource, screen_input

                    tree_screen = screen_input(
                        tree_text, source=InputSource.MCP_OUTPUT, run_id=self.state.run_id
                    )
                    if tree_screen.is_suspicious and tree_screen.sanitized_content:
                        tree_text = tree_screen.sanitized_content

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

        self._save_checkpoint()
        return "analyze_completed"

    @listen(analyze)
    def plan(self) -> str:
        """Step 2: Verify implementation plan produced by Planner agent."""
        if self.state.status in (RunStatus.NEEDS_HUMAN, RunStatus.BLOCKED):
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

        self._save_checkpoint()
        return "plan_completed"

    @listen(or_(plan, "retry"))
    def code(self) -> str:
        """Step 3: Produce code changes (initial implementation or correction iteration)."""
        set_current_run_id(self.state.run_id)
        if self.state.status in (RunStatus.NEEDS_HUMAN, RunStatus.BLOCKED):
            return "needs_human"

        is_retry = self.state.retry_count > 0
        iteration_label = f"Iteration {self.state.retry_count}" if is_retry else "Initial change"

        from repoagent.monitoring import get_event_logger

        with contextlib.suppress(Exception):
            get_event_logger().log_event(
                run_id=self.state.run_id,
                event_type=EventType.AGENT_STARTED,
                agent_name="Senior Python Engineer",
                stage="code",
                input_summary={
                    "requirement": self.state.requirement,
                    "plan": self.state.plan.summary if self.state.plan else "No plan",
                    "iteration": self.state.retry_count,
                    "is_replay": self.state.is_replay,
                },
                metadata={"framework": "CrewAI", "is_replay": self.state.is_replay},
            )

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
            try:
                self.state.changes = self.coding_fn(self.state)
            except Exception as exc:
                from repoagent.security.tool_guardrails import GuardrailViolation

                is_gv = isinstance(exc, GuardrailViolation)
                status_to_set = RunStatus.BLOCKED if is_gv else RunStatus.NEEDS_HUMAN
                err_msg = f"Coding function failed: {exc}"
                self.state.errors.append(err_msg)
                self.state.status = status_to_set
                self.state.finished_at = datetime.now(UTC)
                self._save_checkpoint()
                return "needs_human"
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
            try:
                changes = run_coding_agent(
                    plan=plan,
                    requirement=self.state.requirement,
                    tools=tools,
                    llm=llm,
                    failure_context=failure_context,
                    iteration=self.state.retry_count,
                    workspace=workspace,
                    run_id=self.state.run_id,
                )
                self.state.changes = changes
            except Exception as exc:
                from repoagent.security.tool_guardrails import GuardrailViolation

                is_gv = isinstance(exc, GuardrailViolation)
                status_to_set = RunStatus.BLOCKED if is_gv else RunStatus.NEEDS_HUMAN
                err_msg = f"Coding agent execution failed: {exc}"
                self.state.errors.append(err_msg)
                self.state.status = status_to_set
                self.state.finished_at = datetime.now(UTC)
                self._save_checkpoint()
                return "needs_human"
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
                "+def test_feature_stub(self=None):\n"
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
                apply_changeset_to_workspace(
                    self.state.changes, workspace, run_id=self.state.run_id
                )
        except Exception as exc:
            from repoagent.security.tool_guardrails import GuardrailViolation

            is_gv = isinstance(exc, GuardrailViolation)
            if is_gv:
                err_msg = f"Security guardrail violation in changeset: {exc.reason}"
                self.state.errors.append(err_msg)
                self.state.status = RunStatus.BLOCKED
                self.state.finished_at = datetime.now(UTC)
                if self.state.changes:
                    self.state.rejected_changes.append(self.state.changes)
                self._save_checkpoint()
                return "needs_human"

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

        if self.state.changes:
            changes_dict = {
                "summary": self.state.changes.summary,
                "files": [f.model_dump() for f in self.state.changes.files],
                "replay_label": getattr(self.state, "replay_label", None),
            }
            total_diff_chars = sum(len(f.diff or "") for f in self.state.changes.files)
            with contextlib.suppress(Exception):
                get_event_logger().log_event(
                    run_id=self.state.run_id,
                    event_type=EventType.AGENT_COMPLETED,
                    agent_name="Senior Python Engineer",
                    stage="code",
                    output_summary=changes_dict,
                    metadata={
                        "framework": "CrewAI",
                        "files_count": len(self.state.changes.files),
                        "is_replay": self.state.is_replay,
                        "prompt_tokens": max(1, len(str(self.state.requirement)) // 4),
                        "completion_tokens": max(1, total_diff_chars // 4),
                    },
                )

        return "code_completed"

    @listen(code)
    def test(self) -> str:
        """Step 4: Execute test suite deterministically inside sandbox container."""
        set_current_run_id(self.state.run_id)
        if self.state.status in (RunStatus.NEEDS_HUMAN, RunStatus.BLOCKED):
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
            from repoagent.security import InputSource, screen_input

            log_screen = screen_input(
                raw_text, source=InputSource.MCP_OUTPUT, run_id=self.state.run_id
            )
            if log_screen.is_suspicious and log_screen.sanitized_content:
                report.log_tail = log_screen.sanitized_content
                if report.raw_output:
                    report.raw_output = log_screen.sanitized_content
                raw_text = log_screen.sanitized_content
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
            import subprocess

            proc = subprocess.run(
                ["pytest", "tests/", "-v"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                check=False,
            )
            raw_text = proc.stdout + proc.stderr
            log_lines = raw_text.strip().splitlines()
            log_tail = "\n".join(log_lines[-30:]) if log_lines else ""
            passed = proc.returncode == 0
            failed_tests: list[str] = []
            if not passed:
                for line in log_lines:
                    if "FAILED " in line:
                        parts = line.split()
                        if len(parts) > 1 and "::" in parts[1]:
                            failed_tests.append(parts[1])
                if not failed_tests:
                    failed_tests.append(f"pytest:exit_{proc.returncode}")

            report = TestReport(
                command="pytest tests/ -v",
                passed=passed,
                exit_code=proc.returncode,
                failed_tests=failed_tests,
                log_tail=log_tail,
                duration_s=0.08,
                workspace_hash=ws_hash,
                raw_output=raw_text,
                provenance="offline_fallback",
            )
        else:
            from repoagent.mcp_servers import test_runner

            test_runner._set_workspace(str(workspace))
            report = test_runner.run_tests("pytest")
            report.workspace_hash = ws_hash
            report.provenance = "authoritative_docker_sandbox"

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
        # Screen test runner output to isolate prompt injection instructions
        from repoagent.security import InputSource, screen_input

        log_screen = screen_input(raw_text, source=InputSource.MCP_OUTPUT, run_id=self.state.run_id)
        if log_screen.is_suspicious and log_screen.sanitized_content:
            report.log_tail = log_screen.sanitized_content
            if report.raw_output:
                report.raw_output = log_screen.sanitized_content
            raw_text = log_screen.sanitized_content

        self._save_sandbox_raw(attempt_num, raw_text)

        self.state.test_reports.append(report)

        test_summary_dict = {
            "passed": report.passed,
            "exit_code": report.exit_code,
            "command": report.command,
            "failed_tests": report.failed_tests,
            "duration_s": report.duration_s,
            "provenance": report.provenance,
            "log_tail": report.log_tail[-300:] if report.log_tail else "",
        }
        with contextlib.suppress(Exception):
            get_event_logger().log_event(
                run_id=self.state.run_id,
                event_type=EventType.AGENT_COMPLETED,
                agent_name="docker-test-runner",
                stage="test",
                output_summary=test_summary_dict,
                duration_s=report.duration_s,
                metadata={
                    "passed": report.passed,
                    "provenance": report.provenance,
                },
            )

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
            if not self.state.finished_at:
                self.state.finished_at = datetime.now(UTC)
                self._save_checkpoint()
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
            self.state.errors.append("Execution capped: missing test reports.")
            self._save_checkpoint()
            return "needs_human"

        latest_report = self.state.test_reports[-1]

        if latest_report.passed and bool(latest_report.workspace_hash):
            return "pass"

        # Test failed: check retry budget
        if self.state.retry_count < self.state.max_retries:
            self.state.retry_count += 1
            self._save_checkpoint()
            return "retry"
        else:
            self.state.status = RunStatus.NEEDS_HUMAN
            self.state.finished_at = datetime.now(UTC)
            err_msg = "Execution capped: maximum retries reached after test failure."
            if err_msg not in self.state.errors:
                self.state.errors.append(err_msg)
            self._save_checkpoint()
            return "needs_human"

    @listen("pass")
    def review(self) -> str:
        """Step 5: Review code changes for quality and requirements adherence via ADK Reviewer."""
        set_current_run_id(self.state.run_id)
        if self.state.status in (RunStatus.NEEDS_HUMAN, RunStatus.BLOCKED):
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
                review_report.provenance = "authoritative_adk_reviewer"
                self.state.review = review_report
            except Exception as exc:
                err_msg = f"A2A Reviewer Agent failed: {exc}"
                self.state.errors.append(err_msg)
                self.state.review = ReviewReport(
                    verdict=ReviewVerdict.REQUEST_CHANGES,
                    findings=[ReviewFinding(file="general", severity="critical", message=err_msg)],
                    summary=f"Reviewer execution failure: {err_msg}",
                    provenance="authoritative_adk_reviewer",
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
                provenance="fallback",
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
            if not self.state.finished_at:
                self.state.finished_at = datetime.now(UTC)
                self._save_checkpoint()
            return "needs_human"
        if not self.state.review:
            self.state.status = RunStatus.NEEDS_HUMAN
            self.state.finished_at = datetime.now(UTC)
            self.state.errors.append("Execution capped: missing review report.")
            self._save_checkpoint()
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
            self._save_checkpoint()
            return "retry"
        else:
            self.state.status = RunStatus.NEEDS_HUMAN
            self.state.finished_at = datetime.now(UTC)
            self.state.errors.append(
                "Execution capped: maximum retries reached after code review rejection."
            )
            self._save_checkpoint()
            return "needs_human"

    @listen("approved")
    def open_pr(self) -> str:
        """Step 6: Open GitHub Pull Request for human review if done gate passes."""
        if self.state.status in (RunStatus.NEEDS_HUMAN, RunStatus.BLOCKED):
            return "needs_human"

        workspace = self._get_workspace_path()
        run_dir = (
            Path(self.workspace).parent if self.workspace else get_runs_dir() / self.state.run_id
        )

        # 1. Evaluate DONE Gate conditions (offline mode does not activate mock bypass)
        gate_passed, reason = evaluate_done_gate(
            self.state, workspace, is_mock_test=self.is_mock_test
        )

        if not gate_passed:
            self.state.status = RunStatus.NEEDS_HUMAN
            self.state.finished_at = datetime.now(UTC)
            err_msg = f"Done gate conditions not satisfied: {reason}"
            if err_msg not in self.state.errors:
                self.state.errors.append(err_msg)
            self._save_checkpoint()
            from repoagent.pr_agent import write_run_reports

            write_run_reports(run_dir, self.state)

            from repoagent.monitoring import get_event_logger

            duration_s = (self.state.finished_at - self.state.started_at).total_seconds()
            get_event_logger().log_run_failed(
                run_id=self.state.run_id,
                error=err_msg,
                stage="open_pr",
                duration_s=duration_s,
            )
            return "open_pr_completed"

        # Gate passed: state is authoritatively DONE
        self.state.status = RunStatus.DONE

        # 2. Check if live PR creation is requested
        live_pr_enabled = self.create_pr or os.environ.get("REPOAGENT_LIVE_PR", "").lower() in (
            "true",
            "1",
        )

        if live_pr_enabled:
            from repoagent.pr_agent import open_and_verify_pull_request

            try:
                pr_summary = open_and_verify_pull_request(
                    state=self.state,
                    workspace=workspace,
                    is_live_execution=self.use_live_agents,
                )
                self.state.pr = pr_summary
                self.state.status = RunStatus.DONE
            except Exception as exc:
                self.state.status = RunStatus.NEEDS_HUMAN
                self.state.errors.append(f"PR creation or verification failed: {exc}")
        else:
            from repoagent.pr_agent import build_deterministic_pr_content

            title, body = build_deterministic_pr_content(self.state)
            self.state.pr = PRSummary(
                title=title,
                body_markdown=body,
                url=f"https://github.com/offline-test/repo/pull/{self.state.run_id[:6]}",
                branch=self.state.branch,
            )
            self.state.status = RunStatus.DONE

        self.state.finished_at = datetime.now(UTC)
        self._save_checkpoint()
        from repoagent.pr_agent import write_run_reports

        write_run_reports(run_dir, self.state)

        from repoagent.monitoring import get_event_logger

        duration_s = (self.state.finished_at - self.state.started_at).total_seconds()
        if self.state.status == RunStatus.DONE:
            get_event_logger().log_run_completed(
                run_id=self.state.run_id,
                status=self.state.status.value,
                duration_s=duration_s,
                output_summary={
                    "status": self.state.status.value,
                    "pr_url": self.state.pr.url if self.state.pr else None,
                    "branch": self.state.branch,
                },
            )
        else:
            get_event_logger().log_run_failed(
                run_id=self.state.run_id,
                error=self.state.errors[-1] if self.state.errors else "PR step failed",
                stage="open_pr",
                duration_s=duration_s,
            )
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
        run_dir = (
            Path(self.workspace).parent if self.workspace else get_runs_dir() / self.state.run_id
        )
        from repoagent.pr_agent import write_run_reports

        write_run_reports(run_dir, self.state)

        from repoagent.monitoring import get_event_logger

        duration_s = (self.state.finished_at - self.state.started_at).total_seconds()
        get_event_logger().log_run_failed(
            run_id=self.state.run_id,
            error=self.state.errors[-1] if self.state.errors else msg,
            stage="retry_capped",
            duration_s=duration_s,
        )
        return "needs_human_completed"
