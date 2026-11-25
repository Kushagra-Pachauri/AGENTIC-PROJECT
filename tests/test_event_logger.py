"""Comprehensive tests for the Central Event Logger (Step 7.1).

Covers:
1. Event creation and JSON serialization.
2. Multiple events appended to the same JSONL file.
3. Separate run IDs producing separate logs.
4. Secret redaction (GitHub tokens, API keys, passwords, bearer tokens, .env text).
5. Input/output truncation.
6. Nested data and Pydantic object handling.
7. Non-serializable input handling.
8. Missing or invalid run ID handling.
9. Logging failure handling.
10. Run lifecycle event generation.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from repoagent.flow import RepoAgentFlow
from repoagent.monitoring.event_logger import (
    EventLogger,
    EventType,
)
from repoagent.schemas import (
    ChangeSet,
    FileChange,
    Plan,
    PlannedFile,
    PlannerOutput,
    ReviewReport,
    ReviewVerdict,
    RunState,
    TestReport,
)


class SamplePydanticModel(BaseModel):
    name: str
    token_value: str
    count: int


class TestCentralEventLogger:
    """Test suite for RepoAgent central event logger."""

    def test_event_creation_and_json_serialization(self, tmp_path: Path) -> None:
        """Requirement 1: Verify event creation and strict JSON serialization."""
        logger = EventLogger(runs_dir=tmp_path)
        run_id = "test_run_001"

        success = logger.log_event(
            run_id=run_id,
            event_type=EventType.RUN_STARTED,
            agent_name="Orchestrator",
            stage="lifecycle",
            input_summary={"requirement": "Add password reset"},
            output_summary={"status": "running"},
            duration_s=0.1234,
            metadata={"env": "test"},
        )
        assert success is True

        log_file = tmp_path / run_id / "events.jsonl"
        assert log_file.is_file()

        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

        data = json.loads(lines[0])
        assert data["run_id"] == run_id
        assert data["event_type"] == "RUN_STARTED"
        assert data["agent_name"] == "Orchestrator"
        assert data["stage"] == "lifecycle"
        assert data["input_summary"] == {"requirement": "Add password reset"}
        assert data["output_summary"] == {"status": "running"}
        assert data["duration_s"] == 0.1234
        assert data["metadata"] == {"env": "test"}
        assert "timestamp" in data

    def test_multiple_events_appended_to_same_jsonl_file(self, tmp_path: Path) -> None:
        """Requirement 2: Verify multiple sequential events append to the same run file."""
        logger = EventLogger(runs_dir=tmp_path)
        run_id = "test_run_002"

        logger.log_event(run_id=run_id, event_type=EventType.RUN_STARTED)
        logger.log_event(run_id=run_id, event_type=EventType.TASK_STARTED, stage="analyze")
        logger.log_event(run_id=run_id, event_type=EventType.TASK_COMPLETED, stage="analyze")
        logger.log_event(run_id=run_id, event_type=EventType.RUN_COMPLETED, duration_s=1.5)

        log_file = tmp_path / run_id / "events.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 4

        parsed = [json.loads(line) for line in lines]
        assert [e["event_type"] for e in parsed] == [
            "RUN_STARTED",
            "TASK_STARTED",
            "TASK_COMPLETED",
            "RUN_COMPLETED",
        ]
        assert all(e["run_id"] == run_id for e in parsed)

    def test_separate_run_ids_produce_separate_logs(self, tmp_path: Path) -> None:
        """Requirement 3: Verify separate run IDs produce strictly isolated log files."""
        logger = EventLogger(runs_dir=tmp_path)
        run_a = "run_alpha"
        run_b = "run_beta"

        logger.log_event(run_id=run_a, event_type=EventType.RUN_STARTED, input_summary="alpha")
        logger.log_event(run_id=run_b, event_type=EventType.RUN_STARTED, input_summary="beta")

        file_a = tmp_path / run_a / "events.jsonl"
        file_b = tmp_path / run_b / "events.jsonl"

        assert file_a.is_file()
        assert file_b.is_file()

        lines_a = file_a.read_text(encoding="utf-8").strip().splitlines()
        lines_b = file_b.read_text(encoding="utf-8").strip().splitlines()

        assert len(lines_a) == 1
        assert len(lines_b) == 1

        assert json.loads(lines_a[0])["run_id"] == run_a
        assert json.loads(lines_a[0])["input_summary"] == "alpha"
        assert json.loads(lines_b[0])["run_id"] == run_b
        assert json.loads(lines_b[0])["input_summary"] == "beta"

    def test_secret_redaction(self, tmp_path: Path) -> None:
        """Requirement 4: Verify sensitive tokens, keys, passwords, and .env are redacted."""
        logger = EventLogger(runs_dir=tmp_path)
        run_id = "test_run_redaction"

        secret_gh = "ghp_1234567890abcdef1234567890abcdef1234"
        secret_pat = (
            "github_pat_11AAAAAAA01234567890abcdefghijklmnopqrstuvwxyz"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ12345"
        )
        secret_gemini = "AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz01234"
        secret_openai = "sk-proj-1234567890abcdefghijklmnop"
        secret_bearer = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDcBQACt7Cr"
        env_content = (
            "GITHUB_TOKEN=ghp_secret\nGEMINI_API_KEY=AIzaSySecretKey99\nDATABASE_URL=postgres"
        )

        payload = {
            "github_token": secret_gh,
            "github_pat": secret_pat,
            "gemini_key": secret_gemini,
            "openai_key": secret_openai,
            "auth_header": secret_bearer,
            "env_dump": env_content,
            "nested": {
                "password": "super_secret_password_123!",
                "api_key": "some_random_secret_key",
                "normal_field": "safe_information",
            },
        }

        logger.log_event(
            run_id=run_id,
            event_type=EventType.TOOL_STARTED,
            input_summary=payload,
            output_summary=f"Error with {secret_gh} and {secret_openai}",
        )

        log_file = tmp_path / run_id / "events.jsonl"
        log_content = log_file.read_text(encoding="utf-8")

        # Confirm no raw secret exists anywhere in the raw log file
        assert secret_gh not in log_content
        assert secret_pat not in log_content
        assert secret_gemini not in log_content
        assert secret_openai not in log_content
        assert "super_secret_password_123!" not in log_content
        assert "some_random_secret_key" not in log_content
        assert "ghp_secret" not in log_content

        # Confirm redacted markers are present
        data = json.loads(log_content.strip())
        assert "[REDACTED" in json.dumps(data["input_summary"])
        assert "[REDACTED" in data["output_summary"]
        assert data["input_summary"]["nested"]["normal_field"] == "safe_information"

    def test_input_output_truncation(self, tmp_path: Path) -> None:
        """Requirement 5: Verify oversized input/output values are truncated."""
        logger = EventLogger(runs_dir=tmp_path, max_summary_length=100)
        run_id = "test_run_truncation"

        huge_string = "A" * 500
        logger.log_event(
            run_id=run_id,
            event_type=EventType.TOOL_COMPLETED,
            input_summary=huge_string,
            output_summary={"large_output": "B" * 300, "short": "ok"},
        )

        log_file = tmp_path / run_id / "events.jsonl"
        data = json.loads(log_file.read_text(encoding="utf-8").strip())

        assert len(data["input_summary"]) < 200
        assert "... [truncated 400 chars]" in data["input_summary"]

        large_out = data["output_summary"]["large_output"]
        assert len(large_out) < 200
        assert "... [truncated 200 chars]" in large_out
        assert data["output_summary"]["short"] == "ok"

    def test_nested_data_and_pydantic_object_handling(self, tmp_path: Path) -> None:
        """Requirement 6: Verify nested dicts, lists, and Pydantic models are handled safely."""
        logger = EventLogger(runs_dir=tmp_path)
        run_id = "test_run_pydantic"

        plan = Plan(
            summary="Refactor auth",
            files_to_change=[
                PlannedFile(path="src/notes_app/auth.py", reason="Add token reset"),
            ],
            steps=["Step 1", "Step 2"],
        )
        model = SamplePydanticModel(
            name="TestModel",
            token_value="ghp_1234567890abcdef1234567890abcdef1234",
            count=42,
        )

        logger.log_event(
            run_id=run_id,
            event_type=EventType.TASK_COMPLETED,
            input_summary={"plan": plan, "nested_list": [model, {"sub": "data"}]},
        )

        log_file = tmp_path / run_id / "events.jsonl"
        data = json.loads(log_file.read_text(encoding="utf-8").strip())

        inp = data["input_summary"]
        assert inp["plan"]["summary"] == "Refactor auth"
        assert inp["plan"]["files_to_change"][0]["path"] == "src/notes_app/auth.py"
        assert inp["nested_list"][0]["name"] == "TestModel"
        assert inp["nested_list"][0]["token_value"] == "[REDACTED]"
        assert inp["nested_list"][0]["count"] == 42
        assert inp["nested_list"][1]["sub"] == "data"

    def test_non_serializable_input_handling(self, tmp_path: Path) -> None:
        """Requirement 7: Verify non-serializable objects (sets, functions, etc.) serialize."""
        logger = EventLogger(runs_dir=tmp_path)
        run_id = "test_run_non_serializable"

        def dummy_function() -> None:
            pass

        class CustomClassWithoutDict:
            __slots__ = ("val",)

            def __init__(self, val: str):
                self.val = val

            def __str__(self) -> str:
                return f"Custom({self.val})"

        exc = ValueError("Simulated network timeout")
        raw_bytes = b"binary \x80\x81 data"
        sample_set = {"cherry", "apple", "banana"}

        logger.log_event(
            run_id=run_id,
            event_type=EventType.ERROR,
            error_details=exc,
            input_summary={
                "func": dummy_function,
                "custom": CustomClassWithoutDict("test_val"),
                "bytes": raw_bytes,
                "set": sample_set,
            },
        )

        log_file = tmp_path / run_id / "events.jsonl"
        data = json.loads(log_file.read_text(encoding="utf-8").strip())

        assert "ValueError: Simulated network timeout" in data["error_details"]
        inp = data["input_summary"]
        assert "<callable: dummy_function>" in inp["func"]
        assert "Custom(test_val)" in inp["custom"]
        assert "<binary data" in inp["bytes"]
        assert inp["set"] == ["apple", "banana", "cherry"]

    def test_missing_or_invalid_run_id_handling(self, tmp_path: Path) -> None:
        """Requirement 8: Verify empty or traversal run IDs are rejected safely."""
        logger = EventLogger(runs_dir=tmp_path)

        # 1. Empty string
        assert logger.log_event(run_id="", event_type=EventType.RUN_STARTED) is False

        # 2. Whitespace only
        assert logger.log_event(run_id="   ", event_type=EventType.RUN_STARTED) is False

        # 3. Path traversal attempts
        with pytest.raises(ValueError, match="Invalid run_id"):
            logger.get_log_file_for_run("../escaped")

        with pytest.raises(ValueError, match="Invalid run_id"):
            logger.get_log_file_for_run("run/sub")

        # Ensure no rogue files were created in parent directories
        assert not (tmp_path.parent / "events.jsonl").exists()

    def test_logging_failure_handling(self, tmp_path: Path) -> None:
        """Requirement 9: Verify file system I/O errors do not crash caller and return False."""
        logger = EventLogger(runs_dir=tmp_path)
        run_id = "test_run_failure"

        # Mock Path.open to simulate disk/permission failure
        with patch.object(Path, "open", side_effect=OSError("Disk write permission denied")):
            result = logger.log_event(
                run_id=run_id,
                event_type=EventType.RUN_STARTED,
                input_summary="Will fail",
            )
            # Must return False rather than raising an uncaught exception
            assert result is False

    def test_run_lifecycle_event_generation_in_flow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Requirement 10: Verify RepoAgentFlow logs RUN_STARTED and RUN_COMPLETED/RUN_FAILED."""
        import concurrent.futures

        from repoagent.workspace_utils import setup_workspace

        monkeypatch.setenv("REPOAGENT_RUNS_DIR", str(tmp_path))

        ws = tmp_path / "custom_run" / "workspace"
        setup_workspace("demo_repo", ws)

        def _run_test() -> None:
            # 1. Successful run lifecycle: RUN_STARTED and RUN_COMPLETED
            def mock_coder(st: RunState):
                target_file = ws / "src" / "notes_app" / "auth.py"
                target_file.write_text("# success mod\n", encoding="utf-8")
                return ChangeSet(
                    files=[
                        FileChange(
                            path="src/notes_app/auth.py",
                            action="modify",
                            diff="+ # success mod",
                        )
                    ]
                )

            def mock_tester(st: RunState):
                from repoagent.workspace_utils import compute_workspace_hash

                return TestReport(
                    command="pytest",
                    passed=True,
                    exit_code=0,
                    workspace_hash=compute_workspace_hash(ws),
                )

            def mock_reviewer(plan, diff, test_rep):
                return ReviewReport(verdict=ReviewVerdict.APPROVE, summary="LGTM")

            def mock_planner(r, w):
                return PlannerOutput(
                    repo_map="mock repo map",
                    plan=Plan(
                        summary="mock plan",
                        files_to_change=[PlannedFile(path="src/notes_app/auth.py", reason="test")],
                    ),
                )

            state = RunState(run_id="lifecycle_success", requirement="Add test feature")
            flow = RepoAgentFlow(
                initial_state=state,
                workspace=ws,
                coding_fn=mock_coder,
                testing_fn=mock_tester,
                planner_fn=mock_planner,
                review_fn=mock_reviewer,
                use_live_agents=False,
                create_pr=False,
            )
            flow.kickoff()

            events_file = tmp_path / "lifecycle_success" / "events.jsonl"
            assert events_file.is_file()

            lines = events_file.read_text(encoding="utf-8").strip().splitlines()
            event_types = [json.loads(line)["event_type"] for line in lines]

            assert "RUN_STARTED" in event_types
            assert "RUN_COMPLETED" in event_types

            # 2. Failed run lifecycle: RUN_STARTED and RUN_FAILED
            state_fail = RunState(
                run_id="lifecycle_fail",
                requirement="Add failing feature",
                max_retries=1,
            )
            flow_fail = RepoAgentFlow(
                initial_state=state_fail,
                workspace=ws,
                coding_fn=mock_coder,
                planner_fn=mock_planner,
                simulate_test_failures=3,
                use_live_agents=False,
                create_pr=False,
            )
            flow_fail.kickoff()

            fail_events_file = tmp_path / "lifecycle_fail" / "events.jsonl"
            assert fail_events_file.is_file()

            fail_lines = fail_events_file.read_text(encoding="utf-8").strip().splitlines()
            fail_types = [json.loads(line)["event_type"] for line in fail_lines]

            assert "RUN_STARTED" in fail_types
            assert "RUN_FAILED" in fail_types

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run_test)
            future.result(timeout=20.0)
