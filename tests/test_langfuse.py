"""Comprehensive unit and integration tests for Langfuse distributed tracing in RepoAgent."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from repoagent.monitoring.event_logger import EventLogger, EventType
from repoagent.observability import (
    LangfuseTracker,
    redact_text_for_tracing,
    reset_langfuse_tracker,
    sanitize_data_for_tracing,
)


class TestLangfuseObservability:
    """Test suite verifying Langfuse distributed tracing integration and safety controls."""

    def teardown_method(self) -> None:
        reset_langfuse_tracker(None)

    def test_01_disabled_by_default_without_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When keys are absent and enabled flag is not set, tracing is disabled."""
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)

        tracker = LangfuseTracker()
        assert tracker.is_enabled is False
        assert tracker.start_run_trace(run_id="run_123") is None
        assert tracker.get_trace_url("run_123") is None

    def test_02_enabled_via_env_flag_or_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit LANGFUSE_ENABLED=true or valid key pair enables the tracker."""
        monkeypatch.setenv("LANGFUSE_ENABLED", "true")
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
        monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")

        tracker = LangfuseTracker()
        assert tracker.is_enabled is True
        assert tracker.host == "http://localhost:3000"
        assert tracker.get_trace_url("test_run_456") == (
            "http://localhost:3000/project/default/traces/trace_test_run_456"
        )

    def test_03_trace_creation_and_run_id_correlation(self) -> None:
        """Root trace is created with deterministic run_id correlation."""
        mock_client = MagicMock()
        tracker = LangfuseTracker(public_key="pk", secret_key="sk", enabled=True)
        tracker._client = mock_client

        span = tracker.start_run_trace(
            run_id="run_abc_123",
            requirement="Add payment webhook",
            repo_url="https://github.com/org/repo",
            metadata={"branch": "feature/pay"},
        )
        assert span is not None
        mock_client.start_observation.assert_called_once()
        call_kwargs = mock_client.start_observation.call_args[1]
        assert call_kwargs["name"] == "repoagent-run:run_abc_123"
        assert call_kwargs["trace_id"] == "trace_run_abc_123"
        assert call_kwargs["input"] == "Add payment webhook"
        assert call_kwargs["metadata"]["run_id"] == "run_abc_123"
        assert call_kwargs["metadata"]["branch"] == "feature/pay"

    def test_04_agent_and_tool_span_lifecycle(self) -> None:
        """Agent and tool spans start and end with proper nesting and metadata."""
        mock_client = MagicMock()
        mock_span = MagicMock()
        mock_client.start_observation.return_value = mock_span

        tracker = LangfuseTracker(public_key="pk", secret_key="sk", enabled=True)
        tracker._client = mock_client

        # Start Planner span
        tracker.start_span(
            run_id="run_span_test",
            name="agent:repo_planner:analyze",
            span_type="agent",
            input_data={"req": "Implement auth"},
        )
        # End Planner span
        tracker.end_span(
            run_id="run_span_test",
            name="agent:repo_planner:analyze",
            output_data={"plan": "Plan summary"},
        )
        mock_span.end.assert_called_once()
        end_kwargs = mock_span.end.call_args[1]
        assert end_kwargs["output"] == {"plan": "Plan summary"}

    def test_05_secret_redaction_in_trace_payloads(self) -> None:
        """Secrets are sanitized before dispatch to Langfuse."""
        raw_secret = "ghp_1234567890abcdef1234567890abcdef1234"
        gemini_secret = "AIzaSy" + ("A" * 32)
        sensitive_input = f"curl -H 'Authorization: Bearer {raw_secret}' https://api.github.com"
        sensitive_dict = {
            "token": raw_secret,
            "nested": {"key": gemini_secret},
        }

        # Test string redaction
        redacted_text = redact_text_for_tracing(sensitive_input)
        assert raw_secret not in redacted_text
        assert "[REDACTED" in redacted_text

        # Test structured redaction
        sanitized = sanitize_data_for_tracing(sensitive_dict)
        assert raw_secret not in str(sanitized)
        assert gemini_secret not in str(sanitized)
        assert "[REDACTED" in sanitized["token"]
        assert "[REDACTED" in sanitized["nested"]["key"]

    def test_06_missing_token_and_cost_metadata_not_invented(self) -> None:
        """Generation tracking preserves actual tokens and costs without synthesizing values."""
        mock_client = MagicMock()
        tracker = LangfuseTracker(public_key="pk", secret_key="sk", enabled=True)
        tracker._client = mock_client

        # Record generation when provider does not expose token usage or pricing
        tracker.record_generation(
            run_id="run_no_usage",
            name="ollama_generation",
            model="qwen2.5-coder:3b",
            input_data="Generate unit test",
            output_data="def test_sample(): pass",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
            cost=None,
        )
        call_kwargs = mock_client.start_observation.call_args[1]
        assert call_kwargs["usage"] is None
        assert "calculated_cost_usd" not in call_kwargs["metadata"]

        # Record generation when tokens and cost are legitimately provided
        tracker.record_generation(
            run_id="run_with_usage",
            name="cloud_llm_generation",
            model="gemini-2.0-flash",
            input_data="Analyze repo",
            output_data="Analysis complete",
            prompt_tokens=150,
            completion_tokens=50,
            total_tokens=200,
            cost=0.00015,
        )
        call_kwargs_2 = mock_client.start_observation.call_args[1]
        assert call_kwargs_2["usage"] == {"input": 150, "output": 50, "total": 200}
        assert call_kwargs_2["metadata"]["calculated_cost_usd"] == 0.00015

    def test_07_connection_failure_handling_is_fail_safe(self) -> None:
        """Exceptions in Langfuse client never raise or crash calling workflows."""
        mock_client = MagicMock()
        mock_client.start_observation.side_effect = ConnectionError("Langfuse server unreachable")
        mock_client.flush.side_effect = RuntimeError("Flush buffer write failure")

        tracker = LangfuseTracker(public_key="pk", secret_key="sk", enabled=True)
        tracker._client = mock_client

        # Should log debug message and return None without raising
        result = tracker.start_run_trace(run_id="run_fail_safe", requirement="Test")
        assert result is None

        # Ending span should not raise
        tracker.end_span(run_id="run_fail_safe", name="stage:analyze")

        # Flushing should not raise
        tracker.flush()

    def test_08_event_logger_forwards_events_to_langfuse(self, tmp_path) -> None:
        """EventLogger automatically forwards logged events to active LangfuseTracker."""
        tracker = LangfuseTracker(public_key="pk", secret_key="sk", enabled=True)
        mock_client = MagicMock()
        tracker._client = mock_client
        reset_langfuse_tracker(tracker)

        event_logger = EventLogger(runs_dir=tmp_path)
        run_id = "run_event_logger_fw"

        # 1. RUN_STARTED
        event_logger.log_run_started(run_id=run_id, requirement="Add health endpoint")
        assert f"root:{run_id}" in tracker._active_spans

        # 2. AGENT_STARTED & AGENT_COMPLETED
        event_logger.log_event(
            run_id=run_id,
            event_type=EventType.AGENT_STARTED,
            agent_name="repo_planner",
            stage="analyze",
        )
        event_logger.log_event(
            run_id=run_id,
            event_type=EventType.AGENT_COMPLETED,
            agent_name="repo_planner",
            stage="analyze",
            output_summary={"plan": "ok"},
        )

        # 3. TOOL_STARTED & TOOL_COMPLETED
        event_logger.log_event(
            run_id=run_id,
            event_type=EventType.TOOL_STARTED,
            agent_name="repo_reader_tree",
            stage="analyze",
        )
        event_logger.log_event(
            run_id=run_id,
            event_type=EventType.TOOL_COMPLETED,
            agent_name="repo_reader_tree",
            stage="analyze",
            output_summary="tree",
        )

        # 4. RUN_COMPLETED
        event_logger.log_run_completed(run_id=run_id, status="DONE")
        mock_client.start_observation.assert_called()
