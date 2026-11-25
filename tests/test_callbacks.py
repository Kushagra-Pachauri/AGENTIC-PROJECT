"""Unit and integration tests for Step 7.2: CrewAI and Google ADK callback integration.

Covers all 12 required scenarios:
1. CrewAI agent lifecycle callback logging (AGENT_STARTED, AGENT_COMPLETED).
2. CrewAI task lifecycle callback logging (TASK_STARTED, TASK_COMPLETED).
3. CrewAI tool lifecycle callback logging (TOOL_STARTED, TOOL_COMPLETED, ERROR).
4. ADK Planner callback logging (before_agent_callback, after_agent_callback).
5. ADK Reviewer callback logging (before_agent_callback, after_agent_callback).
6. ADK tool callback logging (before_tool, after_tool, on_tool_error).
7. Correct run_id propagation through contextvars and explicit args.
8. Event schema consistency across both frameworks (valid JSONL, same fields).
9. Fail-safe execution: callback failure does not crash agent execution.
10. Redaction is preserved through callback integration.
11. No duplicate events for a single callback invocation.
12. A2A context propagation remains compatible.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from crewai.tools.base_tool import BaseTool
from repoagent.a2a_client import A2AClient
from repoagent.agents.adk.planner import create_planner_agent
from repoagent.agents.adk.reviewer import create_reviewer_agent
from repoagent.monitoring import (
    Event,
    attach_adk_callbacks,
    create_adk_before_agent_callback,
    create_crewai_after_kickoff,
    create_crewai_before_kickoff,
    create_crewai_task_callback,
    get_event_logger,
    instrument_crewai_tool,
    run_id_context,
)


def _read_events(log_file: Path) -> list[dict[str, Any]]:
    """Helper to parse JSONL lines from an events log file."""
    if not log_file.is_file():
        return []
    return [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]


class DummyTool(BaseTool):
    name: str = "dummy_tool"
    description: str = "A dummy tool for testing instrumentation."

    def _run(self, query: str = "test") -> str:
        if query == "raise_error":
            raise RuntimeError("Tool execution failed on demand.")
        return f"result_for_{query}"


class TestCallbackIntegration:
    """Test suite for CrewAI and Google ADK callback integration with EventLogger."""

    def test_crewai_agent_lifecycle_callbacks(self, tmp_path: Path) -> None:
        """Scenario 1: CrewAI agent lifecycle callback logging (AGENT_STARTED, AGENT_COMPLETED)."""
        run_id = "test_crew_agent_001"
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            before_cb = create_crewai_before_kickoff(
                run_id=run_id, agent_name="Senior Python Engineer", stage="code"
            )
            after_cb = create_crewai_after_kickoff(
                run_id=run_id, agent_name="Senior Python Engineer", stage="code"
            )

            # Trigger before kickoff
            inputs = {"requirement": "Implement caching"}
            ret_inputs = before_cb(inputs)
            assert ret_inputs == inputs

            # Trigger after kickoff
            mock_out = SimpleNamespace(raw="Completed successfully", pydantic=None)
            ret_output = after_cb(mock_out)
            assert ret_output == mock_out

            log_file = tmp_path / run_id / "events.jsonl"
            assert log_file.is_file()
            events = _read_events(log_file)

            types = [e["event_type"] for e in events]
            assert "AGENT_STARTED" in types
            assert "AGENT_COMPLETED" in types

            started_ev = next(e for e in events if e["event_type"] == "AGENT_STARTED")
            assert started_ev["agent_name"] == "Senior Python Engineer"
            assert started_ev["stage"] == "code"
            assert started_ev["input_summary"]["inputs"] == inputs

            completed_ev = next(e for e in events if e["event_type"] == "AGENT_COMPLETED")
            assert completed_ev["agent_name"] == "Senior Python Engineer"
            assert "Completed successfully" in str(completed_ev["output_summary"])

    def test_crewai_task_lifecycle_callbacks(self, tmp_path: Path) -> None:
        """Scenario 2: CrewAI task lifecycle callback logging (TASK_STARTED, TASK_COMPLETED)."""
        run_id = "test_crew_task_002"
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            before_cb = create_crewai_before_kickoff(run_id=run_id)
            task_cb = create_crewai_task_callback(run_id=run_id)

            # Kickoff records TASK_STARTED
            before_cb({"task": "code_refactor"})

            # Task completion records TASK_COMPLETED
            mock_task_output = SimpleNamespace(
                description="Refactor models module",
                raw="Refactored cleanly",
                pydantic=None,
            )
            task_cb(mock_task_output)

            log_file = tmp_path / run_id / "events.jsonl"
            events = _read_events(log_file)

            types = [e["event_type"] for e in events]
            assert "TASK_STARTED" in types
            assert "TASK_COMPLETED" in types

            task_completed = next(e for e in events if e["event_type"] == "TASK_COMPLETED")
            assert task_completed["input_summary"] == "Refactor models module"
            assert task_completed["output_summary"] == "Refactored cleanly"

    def test_crewai_tool_lifecycle_callbacks(self, tmp_path: Path) -> None:
        """Scenario 3: CrewAI tool callback logging (TOOL_STARTED, TOOL_COMPLETED, ERROR)."""
        run_id = "test_crew_tool_003"
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            tool = DummyTool()
            instrumented = instrument_crewai_tool(tool, run_id=run_id, stage="code")

            # 1. Successful execution
            res = instrumented._run(query="search_auth")
            assert res == "result_for_search_auth"

            # 2. Failing execution
            with pytest.raises(RuntimeError, match="Tool execution failed on demand"):
                instrumented._run(query="raise_error")

            log_file = tmp_path / run_id / "events.jsonl"
            events = _read_events(log_file)

            types = [e["event_type"] for e in events]
            assert types.count("TOOL_STARTED") == 2
            assert types.count("TOOL_COMPLETED") == 1
            assert types.count("ERROR") == 1

            completed_ev = next(e for e in events if e["event_type"] == "TOOL_COMPLETED")
            assert completed_ev["output_summary"] == "result_for_search_auth"
            assert completed_ev["duration_s"] is not None

            error_ev = next(e for e in events if e["event_type"] == "ERROR")
            assert "Tool execution failed on demand" in error_ev["error_details"]

    def test_adk_planner_lifecycle_callbacks(self, tmp_path: Path) -> None:
        """Scenario 4: ADK Planner callback logging (before/after agent callbacks)."""
        run_id = "test_adk_planner_004"
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            agent = create_planner_agent(workspace=tmp_path, run_id=run_id)

            assert agent.before_agent_callback is not None
            assert agent.after_agent_callback is not None

            mock_ctx = SimpleNamespace(
                run_id=run_id,
                agent_name="repo_planner",
                user_content="Analyze architecture for auth flow",
                output={"plan": "Step 1: add models"},
            )

            res_before = agent.before_agent_callback(mock_ctx)
            assert res_before is None  # Passive observation

            res_after = agent.after_agent_callback(mock_ctx)
            assert res_after is None  # Passive observation

            log_file = tmp_path / run_id / "events.jsonl"
            events = _read_events(log_file)

            types = [e["event_type"] for e in events]
            assert types == ["AGENT_STARTED", "AGENT_COMPLETED"]

            started = events[0]
            assert started["agent_name"] == "repo_planner"
            assert started["stage"] == "analyze"
            assert "Analyze architecture" in started["input_summary"]

            completed = events[1]
            assert completed["agent_name"] == "repo_planner"
            assert completed["output_summary"] == {"plan": "Step 1: add models"}

    def test_adk_reviewer_lifecycle_callbacks(self, tmp_path: Path) -> None:
        """Scenario 5: ADK Reviewer callback logging (before/after agent callbacks)."""
        run_id = "test_adk_reviewer_005"
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            agent = create_reviewer_agent(run_id=run_id)

            assert agent.before_agent_callback is not None
            assert agent.after_agent_callback is not None

            mock_ctx = SimpleNamespace(
                run_id=run_id,
                agent_name="code_reviewer",
                user_content="Review diff against test report",
                output={"verdict": "approve", "findings": []},
            )

            res_before = agent.before_agent_callback(mock_ctx)
            assert res_before is None

            res_after = agent.after_agent_callback(mock_ctx)
            assert res_after is None

            log_file = tmp_path / run_id / "events.jsonl"
            events = _read_events(log_file)

            types = [e["event_type"] for e in events]
            assert types == ["AGENT_STARTED", "AGENT_COMPLETED"]

            started = events[0]
            assert started["agent_name"] == "code_reviewer"
            assert started["stage"] == "review"

            completed = events[1]
            assert completed["output_summary"] == {"verdict": "approve", "findings": []}

    def test_adk_tool_lifecycle_callbacks(self, tmp_path: Path) -> None:
        """Scenario 6: ADK tool callback logging (before_tool, after_tool, on_tool_error)."""
        run_id = "test_adk_tool_006"
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            mock_agent = SimpleNamespace(name="repo_planner")
            attach_adk_callbacks(
                mock_agent, run_id=run_id, agent_name="repo_planner", stage="analyze"
            )

            mock_tool = SimpleNamespace(name="repo_reader_list_tree")
            mock_ctx = SimpleNamespace(run_id=run_id, agent_name="repo_planner")

            # Before tool
            mock_agent.before_tool_callback(mock_tool, {"path": "."}, mock_ctx)

            # After tool
            mock_agent.after_tool_callback(
                mock_tool, {"path": "."}, mock_ctx, {"tree": ["file1.py", "file2.py"]}
            )

            # Tool error
            mock_agent.on_tool_error_callback(
                mock_tool, {"path": "."}, mock_ctx, RuntimeError("File not found")
            )

            log_file = tmp_path / run_id / "events.jsonl"
            events = _read_events(log_file)

            types = [e["event_type"] for e in events]
            assert types == ["TOOL_STARTED", "TOOL_COMPLETED", "ERROR"]

            assert events[0]["input_summary"] == {
                "tool": "repo_reader_list_tree",
                "arguments": {"path": "."},
            }
            assert events[1]["output_summary"] == {
                "tool": "repo_reader_list_tree",
                "result": {"tree": ["file1.py", "file2.py"]},
            }
            assert "File not found" in events[2]["error_details"]

    def test_run_id_propagation_via_contextvars_and_explicit(self, tmp_path: Path) -> None:
        """Scenario 7: Correct run_id propagation through contextvars and explicit args."""
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            # 1. Explicit run_id overrides context
            with run_id_context("context_run_A"):
                before_cb = create_crewai_before_kickoff(run_id="explicit_run_B")
                before_cb({"input": "test"})

            log_explicit = tmp_path / "explicit_run_B" / "events.jsonl"
            log_context = tmp_path / "context_run_A" / "events.jsonl"
            assert log_explicit.is_file()
            assert not log_context.is_file()

            # 2. No explicit run_id falls back to contextvars
            with run_id_context("context_run_C"):
                before_cb_ctx = create_crewai_before_kickoff(run_id=None)
                before_cb_ctx({"input": "test_ctx"})

            log_context_c = tmp_path / "context_run_C" / "events.jsonl"
            assert log_context_c.is_file()
            events = _read_events(log_context_c)
            assert all(e["run_id"] == "context_run_C" for e in events)

    def test_event_schema_consistency_across_both_frameworks(self, tmp_path: Path) -> None:
        """Scenario 8: Event schema consistency across both CrewAI and Google ADK frameworks."""
        run_id = "test_schema_consistency_008"
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            # Log from CrewAI callback
            crew_cb = create_crewai_before_kickoff(run_id=run_id)
            crew_cb({"input": "crew_input"})

            # Log from ADK callback
            adk_cb = create_adk_before_agent_callback(
                explicit_run_id=run_id, default_agent_name="repo_planner"
            )
            adk_cb(SimpleNamespace(run_id=run_id, user_content="adk_input"))

            log_file = tmp_path / run_id / "events.jsonl"
            lines = log_file.read_text(encoding="utf-8").splitlines()
            assert len(lines) == 3

            core_fields = {"timestamp", "event_type", "run_id", "agent_name", "stage"}
            valid_fields = set(Event.model_fields.keys())

            for line in lines:
                event = json.loads(line)
                # Verify required core fields are present
                assert core_fields.issubset(event.keys())
                # Verify all keys belong to the standard Event schema
                assert set(event.keys()).issubset(valid_fields)
                # Verify full validation via Pydantic model
                validated = Event.model_validate_json(line)
                assert validated.run_id == run_id

    def test_fail_safe_execution_when_callback_fails(self, tmp_path: Path) -> None:
        """Scenario 9: Callback failure does not crash agent execution."""
        run_id = "test_failsafe_009"
        with (
            patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path),
            patch.object(
                get_event_logger(),
                "log_event",
                side_effect=RuntimeError("Disk write failure in logger"),
            ),
        ):
            # 1. CrewAI before kickoff callback
            before_cb = create_crewai_before_kickoff(run_id=run_id)
            ret_in = before_cb({"input": "safe"})
            assert ret_in == {"input": "safe"}

            # 2. CrewAI after kickoff callback
            after_cb = create_crewai_after_kickoff(run_id=run_id)
            ret_out = after_cb("safe_output")
            assert ret_out == "safe_output"

            # 3. CrewAI task callback
            task_cb = create_crewai_task_callback(run_id=run_id)
            task_cb(SimpleNamespace(raw="output"))  # Must not raise

            # 4. CrewAI tool execution
            tool = DummyTool()
            inst = instrument_crewai_tool(tool, run_id=run_id)
            res = inst._run(query="safe_query")
            assert res == "result_for_safe_query"

            # 5. ADK callbacks
            adk_before = create_adk_before_agent_callback(explicit_run_id=run_id)
            assert adk_before(SimpleNamespace()) is None

    def test_redaction_preserved_through_callbacks(self, tmp_path: Path) -> None:
        """Scenario 10: Redaction is preserved through callback integration."""
        run_id = "test_redaction_010"
        secret_token = "ghp_123456789012345678901234567890123456"
        secret_key = "sk-ant-api03-123456789012345678901234567890"

        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            # CrewAI callback with secrets
            before_cb = create_crewai_before_kickoff(run_id=run_id)
            before_cb({"token": secret_token, "prompt": f"Using key {secret_key}"})

            # ADK callback with secrets
            adk_cb = create_adk_before_agent_callback(explicit_run_id=run_id)
            adk_cb(SimpleNamespace(run_id=run_id, user_content=f"Secret token: {secret_token}"))

            log_file = tmp_path / run_id / "events.jsonl"
            content = log_file.read_text(encoding="utf-8")

            # Secrets must be redacted
            assert secret_token not in content
            assert secret_key not in content
            assert "[REDACTED]" in content

    def test_no_duplicate_events_for_single_callback_invocation(self, tmp_path: Path) -> None:
        """Scenario 11: No duplicate events for a single callback invocation."""
        run_id = "test_no_duplicates_011"
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            task_cb = create_crewai_task_callback(run_id=run_id)

            mock_output = SimpleNamespace(description="Task 1", raw="Result 1", pydantic=None)
            # Single call
            task_cb(mock_output)

            log_file = tmp_path / run_id / "events.jsonl"
            lines = log_file.read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 1
            event = json.loads(lines[0])
            assert event["event_type"] == "TASK_COMPLETED"

    def test_a2a_context_propagation_compatibility(self, tmp_path: Path) -> None:
        """Scenario 12: A2A context propagation remains compatible."""
        run_id = "test_a2a_compat_012"
        with patch("repoagent.monitoring.event_logger.get_runs_dir", return_value=tmp_path):
            client = A2AClient(base_url="http://localhost:8001")

            mock_resp = json.dumps(
                {
                    "repo_map": "map",
                    "plan": {
                        "summary": "s",
                        "files_to_change": [],
                        "steps": ["s1"],
                        "risks": [],
                        "test_strategy": "t",
                    },
                }
            )

            with (
                patch.object(client, "send_task", return_value=mock_resp),
                patch("repoagent.mcp_servers.repo_reader.list_tree", return_value="file.py"),
                patch("repoagent.mcp_servers.repo_reader.read_file", return_value="code"),
            ):
                plan_output = client.request_plan(
                    requirement="Add password reset",
                    workspace=tmp_path,
                    run_id=run_id,
                    log_file=tmp_path / run_id / "a2a.jsonl",
                )
                assert plan_output.plan.summary == "s"

            # Check that both a2a.jsonl and events.jsonl were populated for this run_id
            a2a_log = tmp_path / run_id / "a2a.jsonl"
            assert a2a_log.is_file()

            events_log = tmp_path / run_id / "events.jsonl"
            assert events_log.is_file()
            events = _read_events(events_log)
            event_types = [e["event_type"] for e in events]
            assert "TOOL_STARTED" in event_types
            assert "TOOL_COMPLETED" in event_types
            assert all(e["run_id"] == run_id for e in events)
