"""Regression tests for retry, checkpoint atomicity, and background execution reliability."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from repoagent.api.app import (
    _LOCK,
    _RECOVERING_RUNS,
    _RUNNING_FLOWS,
    _execute_run_in_background,
    app,
)
from repoagent.done_gate import evaluate_done_gate
from repoagent.flow import RepoAgentFlow
from repoagent.schemas import (
    ReviewFinding,
    ReviewReport,
    ReviewVerdict,
    RunState,
    RunStatus,
    TestReport,
)


def _setup_test_flow(tmp_path: Path, max_retries: int = 3, retry_count: int = 0) -> RepoAgentFlow:
    """Helper to scaffold an isolated test flow with workspace."""
    run_id = "test_rel_001"
    run_dir = tmp_path / run_id
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    state = RunState(
        run_id=run_id,
        requirement="Test reliability requirement",
        repo_url=str(workspace),
        max_retries=max_retries,
        retry_count=retry_count,
        status=RunStatus.RUNNING,
    )
    flow = RepoAgentFlow(
        initial_state=state,
        workspace=workspace,
        use_live_agents=False,
    )
    flow._save_checkpoint()
    return flow


class TestReliabilityAndCheckpointing:
    """Validate retry, checkpoint persistence, atomicity, and error handling."""

    def test_01_retry_counter_persisted_immediately(self, tmp_path: Path) -> None:
        """Retry increment in route_after_test and route_after_review persists immediately."""
        flow = _setup_test_flow(tmp_path, max_retries=3, retry_count=0)
        run_dir = tmp_path / flow.state.run_id
        state_file = run_dir / "run_state.json"

        # 1. Test failure triggers retry and immediate checkpoint
        failed_report = TestReport(
            command="pytest tests/ -v",
            passed=False,
            exit_code=1,
            failed_tests=["tests/test_app.py::test_failure"],
            workspace_hash="hash_attempt_1",
        )
        flow.state.test_reports.append(failed_report)

        action = flow.route_after_test()
        assert action == "retry"
        assert flow.state.retry_count == 1

        # Verify disk checkpoint reflects the updated retry count immediately
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["retry_count"] == 1
        assert data["status"] == "running"

        # 2. Review rejection triggers second retry and immediate checkpoint
        review_rep = ReviewReport(
            verdict=ReviewVerdict.REQUEST_CHANGES,
            findings=[
                ReviewFinding(file="src/app.py", line=10, severity="high", message="Fix bug")
            ],
            summary="Changes requested",
        )
        flow.state.review = review_rep

        action_rev = flow.route_after_review()
        assert action_rev == "retry"
        assert flow.state.retry_count == 2

        data_rev = json.loads(state_file.read_text(encoding="utf-8"))
        assert data_rev["retry_count"] == 2
        assert len(data_rev["errors"]) > 0

    def test_02_retry_count_never_exceeds_max_retries(self, tmp_path: Path) -> None:
        """Retry count is strictly bounded by max_retries and never exceeds it."""
        flow = _setup_test_flow(tmp_path, max_retries=2, retry_count=2)
        run_dir = tmp_path / flow.state.run_id
        state_file = run_dir / "run_state.json"

        failed_report = TestReport(command="pytest", passed=False, exit_code=1)
        flow.state.test_reports.append(failed_report)

        action = flow.route_after_test()
        assert action == "needs_human"
        assert flow.state.retry_count == 2

        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["retry_count"] == 2
        assert data["status"] == "needs_human"

    def test_03_exhausted_retries_produces_needs_human(self, tmp_path: Path) -> None:
        """Exhausted retries transition to NEEDS_HUMAN and persist to disk."""
        flow = _setup_test_flow(tmp_path, max_retries=1, retry_count=1)
        run_dir = tmp_path / flow.state.run_id
        state_file = run_dir / "run_state.json"

        failed_report = TestReport(command="pytest", passed=False, exit_code=1)
        flow.state.test_reports.append(failed_report)

        action = flow.route_after_test()
        assert action == "needs_human"
        assert flow.state.status == RunStatus.NEEDS_HUMAN

        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["status"] == "needs_human"
        assert data["finished_at"] is not None

    def test_04_terminal_branches_persist_finished_at(self, tmp_path: Path) -> None:
        """All terminal transitions record finished_at and persist it."""
        flow = _setup_test_flow(tmp_path, max_retries=1, retry_count=1)
        run_dir = tmp_path / flow.state.run_id
        state_file = run_dir / "run_state.json"

        failed_report = TestReport(command="pytest", passed=False, exit_code=1)
        flow.state.test_reports.append(failed_report)

        flow.route_after_test()
        assert flow.state.finished_at is not None

        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["finished_at"] is not None

    def test_05_missing_test_reports_produces_persisted_terminal_state(
        self, tmp_path: Path
    ) -> None:
        """Absence of test reports halts execution and persists NEEDS_HUMAN terminal state."""
        flow = _setup_test_flow(tmp_path, max_retries=3, retry_count=0)
        run_dir = tmp_path / flow.state.run_id
        state_file = run_dir / "run_state.json"

        flow.state.test_reports = []
        action = flow.route_after_test()
        assert action == "needs_human"
        assert flow.state.status == RunStatus.NEEDS_HUMAN
        assert flow.state.finished_at is not None

        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["status"] == "needs_human"
        assert data["finished_at"] is not None
        assert any("missing test reports" in e.lower() for e in data["errors"])

    def test_06_checkpoint_write_failures_logged_and_observable(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Checkpoint serialization/write errors are logged with run ID and return False."""
        flow = _setup_test_flow(tmp_path)

        with (
            caplog.at_level(logging.ERROR),
            patch("os.replace", side_effect=OSError("Disk write simulated failure")),
        ):
            success = flow._save_checkpoint()
            assert success is False
            assert f"Failed to save checkpoint for run {flow.state.run_id}" in caplog.text

    def test_07_atomic_checkpoint_writing_preserves_previous_file(self, tmp_path: Path) -> None:
        """Atomic replacement prevents corrupting valid existing run_state on failure."""
        flow = _setup_test_flow(tmp_path)
        run_dir = tmp_path / flow.state.run_id
        state_file = run_dir / "run_state.json"

        # Verify initial valid state
        assert state_file.is_file()
        initial_content = state_file.read_text(encoding="utf-8")
        assert '"retry_count": 0' in initial_content

        # Mutate in-memory state but simulate crash during replacement
        flow.state.retry_count = 99
        with patch("os.replace", side_effect=PermissionError("File locked")):
            success = flow._save_checkpoint()
            assert success is False

        # Verify the file on disk was preserved completely intact
        current_content = state_file.read_text(encoding="utf-8")
        assert current_content == initial_content
        assert '"retry_count": 0' in current_content
        assert '"retry_count": 99' not in current_content


class TestBackgroundAndStaleRunHandling:
    """Validate background execution lifecycle and stale run management."""

    def test_08_background_execution_exceptions_produce_terminal_state(
        self, tmp_path: Path
    ) -> None:
        """Exceptions in background thread transition state to NEEDS_HUMAN with sanitized error."""
        flow = _setup_test_flow(tmp_path)
        run_id = flow.state.run_id
        run_dir = tmp_path / run_id
        state_file = run_dir / "run_state.json"

        secret_err = "Failure: token=ghp_super_secret_token_1234567890"
        with patch.object(flow, "kickoff", side_effect=RuntimeError(secret_err)):
            _execute_run_in_background(flow)

        assert flow.state.status == RunStatus.NEEDS_HUMAN
        assert flow.state.finished_at is not None
        assert "ghp_super_secret_token_1234567890" not in "".join(flow.state.errors)

        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["status"] == "needs_human"
        assert data["finished_at"] is not None
        assert "ghp_super_secret_token_1234567890" not in json.dumps(data)

    def test_09_background_flow_registry_cleanup_even_after_exceptions(
        self, tmp_path: Path
    ) -> None:
        """_RUNNING_FLOWS registry is safely pruned even when kickoff raises an exception."""
        flow = _setup_test_flow(tmp_path)
        run_id = flow.state.run_id

        with patch.object(flow, "kickoff", side_effect=ValueError("Boom")):
            _execute_run_in_background(flow)

        with _LOCK:
            assert run_id not in _RUNNING_FLOWS

    def test_10_existing_done_gate_and_pr_safety_unchanged(self, tmp_path: Path) -> None:
        """DONE gate evaluation conditions remain strict and unchanged."""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        # 1. Incomplete state fails gate
        incomplete_state = RunState(run_id="run_g1", requirement="Feature", repo_url=str(workspace))
        passed, reason = evaluate_done_gate(incomplete_state, workspace, is_mock_test=True)
        assert passed is False
        assert "missing" in reason.lower()

        # 2. State with failed test report fails gate
        fail_rep = TestReport(command="pytest", passed=False, exit_code=1)
        state_with_fail = RunState(
            run_id="run_g2",
            requirement="Feature",
            repo_url=str(workspace),
            test_reports=[fail_rep],
        )
        passed2, reason2 = evaluate_done_gate(state_with_fail, workspace, is_mock_test=True)
        assert passed2 is False
        assert "failed" in reason2.lower()

        # 3. NEEDS_HUMAN state is non-terminal for DONE gate
        state_with_fail.status = RunStatus.NEEDS_HUMAN
        passed3, _ = evaluate_done_gate(state_with_fail, workspace, is_mock_test=True)
        assert passed3 is False

    def test_11_stale_running_records_identified_without_auto_resumption(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stale persisted RUNNING runs are identified as is_stale=True and recovered explicitly."""
        monkeypatch.setenv("REPOAGENT_RUNS_DIR", str(tmp_path))
        run_id = "test_stale_run_101"
        run_dir = tmp_path / run_id
        run_dir.mkdir(parents=True)

        state = RunState(
            run_id=run_id,
            requirement="Feature before crash",
            status=RunStatus.RUNNING,
            repo_url="demo_repo",
            started_at=datetime.now(UTC),
        )
        state_file = run_dir / "run_state.json"
        state_file.write_text(state.model_dump_json(indent=2), encoding="utf-8")

        client = TestClient(app)

        # 1. GET /runs displays is_stale=True without mutating disk
        resp = client.get("/runs")
        assert resp.status_code == 200
        summaries = resp.json()
        target_sum = next(s for s in summaries if s["run_id"] == run_id)
        assert target_sum["status"] == "running"
        assert target_sum["is_active"] is False
        assert target_sum["is_stale"] is True

        # Verify disk file is still RUNNING (no automated modification on read)
        disk_data = json.loads(state_file.read_text(encoding="utf-8"))
        assert disk_data["status"] == "running"
        assert disk_data.get("finished_at") is None

        # 2. GET /runs/{run_id} also confirms is_stale=True
        detail_resp = client.get(f"/runs/{run_id}")
        assert detail_resp.status_code == 200
        detail_data = detail_resp.json()
        assert detail_data["status"] == "running"
        assert detail_data["is_active"] is False
        assert detail_data["is_stale"] is True

        # 3. Explicit recovery endpoint transitions run to NEEDS_HUMAN
        rec_resp = client.post(f"/runs/{run_id}/recover")
        assert rec_resp.status_code == 200
        rec_data = rec_resp.json()
        assert rec_data["status"] == "needs_human"
        assert rec_data["is_active"] is False
        assert rec_data["is_stale"] is False
        assert rec_data["finished_at"] is not None

        # Verify disk was updated safely
        updated_disk = json.loads(state_file.read_text(encoding="utf-8"))
        assert updated_disk["status"] == "needs_human"
        assert updated_disk["finished_at"] is not None
        assert any("interrupted" in err.lower() for err in updated_disk["errors"])

        # 4. Attempting to recover an already-recovered run returns 400
        rec_again = client.post(f"/runs/{run_id}/recover")
        assert rec_again.status_code == 400

    def test_12_recovering_active_run_returns_409(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Attempting to recover an actively running in-memory flow returns HTTP 409 Conflict."""
        monkeypatch.setenv("REPOAGENT_RUNS_DIR", str(tmp_path))
        flow = _setup_test_flow(tmp_path)
        run_id = flow.state.run_id
        run_dir = tmp_path / run_id
        state_file = run_dir / "run_state.json"
        initial_content = state_file.read_text(encoding="utf-8")

        with _LOCK:
            _RUNNING_FLOWS[run_id] = flow

        try:
            client = TestClient(app)
            resp = client.post(f"/runs/{run_id}/recover")
            assert resp.status_code == 409
            assert "active in memory" in resp.json()["detail"].lower()

            # State file must not be modified
            assert state_file.read_text(encoding="utf-8") == initial_content
        finally:
            with _LOCK:
                _RUNNING_FLOWS.pop(run_id, None)

    def test_13_recovery_cannot_race_with_active_registration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Active registration and stale recovery are mutually exclusive."""
        monkeypatch.setenv("REPOAGENT_RUNS_DIR", str(tmp_path))
        flow = _setup_test_flow(tmp_path)
        run_id = flow.state.run_id

        # 1. While in _RECOVERING_RUNS, background execution cannot register as active
        with _LOCK:
            _RECOVERING_RUNS.add(run_id)

        try:
            _execute_run_in_background(flow)
            with _LOCK:
                assert run_id not in _RUNNING_FLOWS

            assert flow.state.status == RunStatus.NEEDS_HUMAN
            assert any("undergoing recovery" in e.lower() for e in flow.state.errors)

            # Concurrent recovery requests also return HTTP 409
            client = TestClient(app)
            resp = client.post(f"/runs/{run_id}/recover")
            assert resp.status_code == 409
            assert "undergoing recovery" in resp.json()["detail"].lower()
        finally:
            with _LOCK:
                _RECOVERING_RUNS.discard(run_id)

    def test_14_atomic_checkpoint_cleans_up_tmp_files_on_failure(self, tmp_path: Path) -> None:
        """Temp files are cleaned up on failure, leaving existing files intact without orphans."""
        flow = _setup_test_flow(tmp_path)
        run_dir = tmp_path / flow.state.run_id
        reports_dir = run_dir / "test_reports"

        # Populate state with test reports and perform initial successful save
        flow.state.test_reports = [
            TestReport(command="pytest", passed=True, exit_code=0, workspace_hash="h1"),
            TestReport(command="pytest", passed=False, exit_code=1, workspace_hash="h2"),
        ]
        assert flow._save_checkpoint() is True

        state_file = run_dir / "run_state.json"
        rep1_file = reports_dir / "test_report_1.json"
        rep2_file = reports_dir / "test_report_2.json"
        assert state_file.is_file()
        assert rep1_file.is_file()
        assert rep2_file.is_file()

        initial_state = state_file.read_text(encoding="utf-8")
        initial_rep1 = rep1_file.read_text(encoding="utf-8")

        # Mutate in-memory state
        flow.state.retry_count = 5

        # Simulate failure during atomic replacement
        with patch("os.replace", side_effect=OSError("Disk write error")):
            result = flow._save_checkpoint()
            assert result is False

        # Confirm no temporary files exist anywhere in run_dir
        tmp_files = list(run_dir.rglob("*.tmp*"))
        assert len(tmp_files) == 0

        # Existing files must remain intact and unmodified
        assert state_file.read_text(encoding="utf-8") == initial_state
        assert rep1_file.read_text(encoding="utf-8") == initial_rep1

    def test_15_background_exception_redacts_secrets_in_logs_and_state(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Secrets in background execution exceptions are redacted from logs and persisted state."""
        flow = _setup_test_flow(tmp_path)
        run_id = flow.state.run_id
        run_dir = tmp_path / run_id
        state_file = run_dir / "run_state.json"

        secret_token = "ghp_verySecretGitHubToken1234567890abcdef"
        raw_exc_msg = f"Fatal connection error with token {secret_token}"

        with (
            caplog.at_level(logging.ERROR),
            patch.object(flow, "kickoff", side_effect=RuntimeError(raw_exc_msg)),
        ):
            _execute_run_in_background(flow)

        assert flow.state.status == RunStatus.NEEDS_HUMAN
        assert flow.state.finished_at is not None

        # Verify secret token is NOT in memory errors
        errors_str = " ".join(flow.state.errors)
        assert secret_token not in errors_str
        assert "[REDACTED_GITHUB_TOKEN]" in errors_str

        # Verify secret token is NOT in captured logs
        assert secret_token not in caplog.text
        assert "[REDACTED_GITHUB_TOKEN]" in caplog.text
        assert run_id in caplog.text

        # Verify secret token is NOT in persisted state on disk
        persisted_data = json.loads(state_file.read_text(encoding="utf-8"))
        assert secret_token not in json.dumps(persisted_data)
