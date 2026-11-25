"""Centralized structured event logging system for RepoAgent.

Every event is recorded in a run-specific JSON Lines (JSONL) file:
    runs/<run_id>/events.jsonl

Provides automated credential redaction, summary truncation, and safe
serialization of nested Pydantic models and arbitrary Python objects.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from repoagent.workspace_utils import get_runs_dir

logger = logging.getLogger(__name__)

# Common secret patterns for automated redaction
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # GitHub Personal Access Tokens (Classic & Fine-grained)
    (re.compile(r"ghp_[A-Za-z0-9_]{30,}", re.IGNORECASE), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{40,}", re.IGNORECASE), "[REDACTED_GITHUB_PAT]"),
    # Generic Bearer tokens
    (
        re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{16,}", re.IGNORECASE),
        "Bearer [REDACTED_BEARER_TOKEN]",
    ),
    # Google Gemini API keys
    (re.compile(r"AIzaSy[A-Za-z0-9_\-]{30,}", re.IGNORECASE), "[REDACTED_GEMINI_KEY]"),
    # OpenAI API keys
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}", re.IGNORECASE), "[REDACTED_OPENAI_KEY]"),
    # Generic key/secret assignments (e.g. in JSON or query params)
    (
        re.compile(
            r"""(?i)(["']?(?:api[_-]?key|secret|token|password|auth(?:entication)?|passwd)["']?\s*[:=]\s*["']?)([^"'&\s,]+)(["']?)"""
        ),
        r"\g<1>[REDACTED]\g<3>",
    ),
    # Common .env assignments
    (
        re.compile(
            r"""(?i)(GITHUB_TOKEN|GEMINI_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|AWS_SECRET_ACCESS_KEY)\s*=\s*([^\s]+)"""
        ),
        r"\g<1>=[REDACTED]",
    ),
]


class EventType(str, Enum):
    """Supported event types in the RepoAgent monitoring lifecycle."""

    RUN_STARTED = "RUN_STARTED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    ERROR = "ERROR"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"

    # Input guardrails & prompt injection events
    INPUT_SCREENING_STARTED = "INPUT_SCREENING_STARTED"
    INPUT_ALLOWED = "INPUT_ALLOWED"
    INPUT_FLAGGED = "INPUT_FLAGGED"
    INPUT_BLOCKED = "INPUT_BLOCKED"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"
    SUSPICIOUS_CONTENT_DETECTED = "SUSPICIOUS_CONTENT_DETECTED"

    # Step 7.4: Tool execution guardrails & ChangeSet validation events
    TOOL_GUARD_PASSED = "TOOL_GUARD_PASSED"
    TOOL_GUARD_BLOCKED = "TOOL_GUARD_BLOCKED"
    CHANGESET_VALIDATION_PASSED = "CHANGESET_VALIDATION_PASSED"
    CHANGESET_VALIDATION_FAILED = "CHANGESET_VALIDATION_FAILED"
    SECRET_SCAN_BLOCKED = "SECRET_SCAN_BLOCKED"


class Event(BaseModel):
    """Structured representation of a single telemetry / audit event."""

    timestamp: str
    run_id: str
    event_type: str
    agent_name: str | None = None
    stage: str | None = None
    input_summary: Any = None
    output_summary: Any = None
    duration_s: float | None = None
    error_details: Any = None
    metadata: dict[str, Any] | None = None


def redact_secrets(val: Any) -> Any:
    """Recursively redact credential patterns from strings, dicts, lists, and models."""
    if isinstance(val, str):
        redacted = val
        for pattern, repl in _SECRET_PATTERNS:
            redacted = pattern.sub(repl, redacted)
        return redacted
    if isinstance(val, BaseModel):
        return redact_secrets(val.model_dump())
    if isinstance(val, dict):
        sanitized_dict: dict[str, Any] = {}
        for k, v in val.items():
            k_str = str(k)
            # Mask value directly if key name suggests a credential (exclude token count metrics)
            is_token_metric = k_str.lower().endswith("_tokens") or k_str.lower() in (
                "tokens",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            )
            if not is_token_metric and any(
                secret_word in k_str.lower()
                for secret_word in ("token", "password", "secret", "api_key", "apikey", "auth")
            ):
                sanitized_dict[k_str] = "[REDACTED]"
            else:
                sanitized_dict[k_str] = redact_secrets(v)
        return sanitized_dict
    if isinstance(val, list):
        return [redact_secrets(item) for item in val]
    if isinstance(val, tuple):
        return tuple(redact_secrets(item) for item in val)
    if isinstance(val, set):
        return [redact_secrets(item) for item in sorted(val, key=str)]
    return val


def truncate_value(val: Any, max_length: int = 2000) -> Any:
    """Truncate long string representations while preserving valid structure."""
    if isinstance(val, str):
        if len(val) > max_length:
            return f"{val[:max_length]}... [truncated {len(val) - max_length} chars]"
        return val
    if isinstance(val, dict):
        return {str(k): truncate_value(v, max_length=max_length) for k, v in val.items()}
    if isinstance(val, list):
        return [truncate_value(item, max_length=max_length) for item in val]
    return val


def make_json_safe(val: Any, max_length: int = 2000) -> Any:
    """Transform arbitrary Python objects into JSON-serializable, redacted, truncated data."""
    if val is None:
        return None
    if isinstance(val, BaseModel):
        val = val.model_dump()
    elif isinstance(val, datetime | Path):
        return str(val)
    elif isinstance(val, set | frozenset):
        val = sorted(list(val), key=str)
    elif isinstance(val, bytes):
        try:
            val = val.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary data: {len(val)} bytes>"
    elif isinstance(val, Exception):
        return f"{type(val).__name__}: {val!s}"
    elif callable(val):
        return f"<callable: {getattr(val, '__name__', str(val))}>"

    # Recursive dictionary conversion
    if isinstance(val, dict):
        sanitized_dict: dict[str, Any] = {}
        for k, v in val.items():
            k_str = str(k)
            is_token_metric = k_str.lower().endswith("_tokens") or k_str.lower() in (
                "tokens",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            )
            if not is_token_metric and any(
                secret_word in k_str.lower()
                for secret_word in ("token", "password", "secret", "api_key", "apikey", "auth")
            ):
                sanitized_dict[k_str] = "[REDACTED]"
            else:
                sanitized_dict[k_str] = make_json_safe(v, max_length=max_length)
        return sanitized_dict

    # Recursive list / tuple conversion
    if isinstance(val, list | tuple):
        return [make_json_safe(item, max_length=max_length) for item in val]

    # Redact credentials
    redacted = redact_secrets(val)

    # Truncate oversized strings
    truncated = truncate_value(redacted, max_length=max_length)
    return truncated


class EventLogger:
    """Centralized, safe event logger for RepoAgent runs.

    Writes to `runs/<run_id>/events.jsonl` in an append-only, fail-safe manner.
    """

    def __init__(
        self,
        runs_dir: Path | str | None = None,
        max_summary_length: int = 2000,
    ) -> None:
        self._runs_dir = Path(runs_dir).resolve() if runs_dir else None
        self.max_summary_length = max_summary_length

    def _resolve_runs_dir(self) -> Path:
        """Resolve base runs directory."""
        if self._runs_dir:
            return self._runs_dir
        return get_runs_dir()

    def get_log_file_for_run(self, run_id: str) -> Path:
        """Get the absolute path to the events.jsonl file for a run."""
        clean_run_id = str(run_id).strip()
        is_invalid = (
            not clean_run_id
            or clean_run_id in (".", "..")
            or "/" in clean_run_id
            or "\\" in clean_run_id
        )
        if is_invalid:
            raise ValueError(f"Invalid run_id for event logging: '{run_id}'")
        return self._resolve_runs_dir() / clean_run_id / "events.jsonl"

    def log_event(
        self,
        run_id: str,
        event_type: EventType | str,
        *,
        agent_name: str | None = None,
        stage: str | None = None,
        input_summary: Any = None,
        output_summary: Any = None,
        duration_s: float | None = None,
        error_details: Any = None,
        metadata: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        """Record an event to the run's events.jsonl log.

        Returns True if successfully written, False otherwise.
        Never raises exceptions to calling workflows.
        """
        try:
            # Validate run_id
            if not run_id or not isinstance(run_id, str) or not run_id.strip():
                logger.error("EventLogger: Missing or empty run_id. Event not recorded.")
                return False

            clean_run_id = run_id.strip()
            log_path = self.get_log_file_for_run(clean_run_id)

            ts_str = (
                timestamp.astimezone(UTC).isoformat()
                if timestamp
                else datetime.now(UTC).isoformat()
            )
            event_type_str = (
                event_type.value if isinstance(event_type, EventType) else str(event_type)
            )

            # Sanitize and serialize summaries
            safe_input = make_json_safe(input_summary, max_length=self.max_summary_length)
            safe_output = make_json_safe(output_summary, max_length=self.max_summary_length)
            safe_error = make_json_safe(error_details, max_length=self.max_summary_length)
            safe_meta = make_json_safe(metadata, max_length=self.max_summary_length)

            entry: dict[str, Any] = {
                "timestamp": ts_str,
                "run_id": clean_run_id,
                "event_type": event_type_str,
            }
            if agent_name:
                entry["agent_name"] = str(agent_name)
            if stage:
                entry["stage"] = str(stage)
            if safe_input is not None:
                entry["input_summary"] = safe_input
            if safe_output is not None:
                entry["output_summary"] = safe_output
            if duration_s is not None:
                with contextlib.suppress(ValueError, TypeError):
                    entry["duration_s"] = round(float(duration_s), 4)
            if safe_error is not None:
                entry["error_details"] = safe_error
            if safe_meta:
                entry["metadata"] = safe_meta

            log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry, default=str)

            with log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

            # Forward event to Langfuse distributed tracing (fail-safe)
            try:
                from repoagent.observability import get_langfuse_tracker

                tracker = get_langfuse_tracker()
                if tracker.is_enabled:
                    event_obj = Event(**entry)
                    tracker.on_event(event_obj)
            except Exception as e:
                logger.debug("Langfuse notification skipped fail-safely: %s", e)

            return True

        except Exception as exc:
            # Fail-safe handling: log error without crashing caller or leaking secrets
            logger.error("EventLogger failed to log event for run '%s': %s", run_id, exc)
            return False

    def log_run_started(
        self,
        run_id: str,
        requirement: str,
        repo_url: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Convenience helper to record a RUN_STARTED event."""
        return self.log_event(
            run_id=run_id,
            event_type=EventType.RUN_STARTED,
            stage="lifecycle",
            input_summary={"requirement": requirement, "repo_url": repo_url},
            metadata=metadata,
        )

    def log_run_completed(
        self,
        run_id: str,
        status: str = "done",
        duration_s: float | None = None,
        output_summary: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Convenience helper to record a RUN_COMPLETED event."""
        return self.log_event(
            run_id=run_id,
            event_type=EventType.RUN_COMPLETED,
            stage="lifecycle",
            duration_s=duration_s,
            output_summary=output_summary or {"status": status},
            metadata=metadata,
        )

    def log_run_failed(
        self,
        run_id: str,
        error: Any,
        stage: str | None = None,
        duration_s: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Convenience helper to record a RUN_FAILED event."""
        return self.log_event(
            run_id=run_id,
            event_type=EventType.RUN_FAILED,
            stage=stage or "lifecycle",
            duration_s=duration_s,
            error_details=error,
            metadata=metadata,
        )

    def log_guardrail_event(
        self,
        run_id: str,
        event_type: EventType | str,
        source: str,
        decision: str,
        category: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Convenience helper to record a security guardrail event."""
        meta = {"source": source, "decision": decision, "category": category}
        if metadata:
            meta.update(metadata)
        return self.log_event(
            run_id=run_id,
            event_type=event_type,
            agent_name="InputGuardrails",
            stage="guardrails",
            input_summary={"source": source, "category": category, "reason": reason},
            metadata=meta,
        )

    def log_tool_guard_event(
        self,
        run_id: str,
        event_type: EventType | str,
        tool_name: str,
        decision: str,
        reason: str,
        category: str = "TOOL_GUARD",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Convenience helper to record a tool execution guardrail event."""
        meta = {
            "tool_name": tool_name,
            "decision": decision,
            "category": category,
            "reason": reason,
        }
        if metadata:
            meta.update(metadata)
        return self.log_event(
            run_id=run_id,
            event_type=event_type,
            agent_name="ToolGuardrails",
            stage="tool_execution",
            input_summary={"tool": tool_name, "decision": decision, "reason": reason},
            metadata=meta,
        )

    def log_changeset_validation_event(
        self,
        run_id: str,
        event_type: EventType | str,
        decision: str,
        reason: str,
        category: str = "CHANGESET_VALIDATION",
        file_count: int = 0,
        diff_size_bytes: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Convenience helper to record a ChangeSet validation event."""
        meta = {
            "decision": decision,
            "category": category,
            "reason": reason,
            "file_count": file_count,
            "diff_size_bytes": diff_size_bytes,
        }
        if metadata:
            meta.update(metadata)
        return self.log_event(
            run_id=run_id,
            event_type=event_type,
            agent_name="ChangeSetValidator",
            stage="changeset_validation",
            input_summary={
                "decision": decision,
                "reason": reason,
                "file_count": file_count,
                "diff_size_bytes": diff_size_bytes,
            },
            metadata=meta,
        )


_DEFAULT_LOGGER: EventLogger | None = None


def get_event_logger() -> EventLogger:
    """Get the singleton default EventLogger instance."""
    global _DEFAULT_LOGGER
    if _DEFAULT_LOGGER is None:
        _DEFAULT_LOGGER = EventLogger()
    return _DEFAULT_LOGGER
