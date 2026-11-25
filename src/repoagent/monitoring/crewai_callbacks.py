"""CrewAI callback integration for RepoAgent centralized event logging.

Captures agent, task, and tool lifecycle events during CrewAI execution
and records them to `runs/<run_id>/events.jsonl` via EventLogger.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repoagent.monitoring.context import resolve_run_id, resolve_workspace
from repoagent.monitoring.event_logger import EventType, get_event_logger

if TYPE_CHECKING:
    from crewai.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


def create_crewai_before_kickoff(
    run_id: str | None = None,
    agent_name: str = "Senior Python Engineer",
    stage: str = "code",
) -> Callable[[dict[str, Any] | None], dict[str, Any] | None]:
    """Build a callback executed before CrewAI crew kickoff to log start events."""

    def _before_kickoff(inputs: dict[str, Any] | None) -> dict[str, Any] | None:
        try:
            active_run_id = resolve_run_id(run_id)
            event_logger = get_event_logger()
            # 1. Log AGENT_STARTED
            event_logger.log_event(
                run_id=active_run_id,
                event_type=EventType.AGENT_STARTED,
                agent_name=agent_name,
                stage=stage,
                input_summary={"framework": "CrewAI", "inputs": inputs},
            )
            # 2. Log TASK_STARTED
            event_logger.log_event(
                run_id=active_run_id,
                event_type=EventType.TASK_STARTED,
                agent_name=agent_name,
                stage=stage,
                input_summary="CrewAI coding task started",
            )
        except Exception as exc:
            logger.error("Error in CrewAI before_kickoff callback: %s", exc)
        return inputs

    return _before_kickoff


def create_crewai_after_kickoff(
    run_id: str | None = None,
    agent_name: str = "Senior Python Engineer",
    stage: str = "code",
) -> Callable[[Any], Any]:
    """Build a callback executed after CrewAI crew kickoff to log completion."""

    def _after_kickoff(output: Any) -> Any:
        try:
            active_run_id = resolve_run_id(run_id)
            event_logger = get_event_logger()
            out_summary: Any
            if hasattr(output, "pydantic") and output.pydantic:
                out_summary = output.pydantic
            elif hasattr(output, "raw"):
                out_summary = output.raw
            else:
                out_summary = str(output)

            event_logger.log_event(
                run_id=active_run_id,
                event_type=EventType.AGENT_COMPLETED,
                agent_name=agent_name,
                stage=stage,
                output_summary=out_summary,
            )
        except Exception as exc:
            logger.error("Error in CrewAI after_kickoff callback: %s", exc)
        return output

    return _after_kickoff


def create_crewai_task_callback(
    run_id: str | None = None,
    agent_name: str = "Senior Python Engineer",
    stage: str = "code",
) -> Callable[[Any], None]:
    """Build a callback executed when a CrewAI Task completes."""

    def _task_callback(task_output: Any) -> None:
        try:
            active_run_id = resolve_run_id(run_id)
            event_logger = get_event_logger()

            out_summary: Any
            if hasattr(task_output, "pydantic") and task_output.pydantic:
                out_summary = task_output.pydantic
            elif hasattr(task_output, "raw"):
                out_summary = task_output.raw
            else:
                out_summary = str(task_output)

            in_summary = getattr(task_output, "description", "CrewAI Task")

            event_logger.log_event(
                run_id=active_run_id,
                event_type=EventType.TASK_COMPLETED,
                agent_name=agent_name,
                stage=stage,
                input_summary=in_summary,
                output_summary=out_summary,
            )
        except Exception as exc:
            logger.error("Error in CrewAI task_callback: %s", exc)

    return _task_callback


def create_crewai_step_callback(
    run_id: str | None = None,
    agent_name: str = "Senior Python Engineer",
    stage: str = "code",
) -> Callable[[Any], None]:
    """Build a callback executed after each agent step in CrewAI."""

    def _step_callback(step_output: Any) -> None:
        try:
            active_run_id = resolve_run_id(run_id)
            event_logger = get_event_logger()

            # Identify if step corresponds to tool output or agent thought
            step_text = str(step_output)
            is_tool = any(k in step_text.lower() for k in ("tool", "result", "action"))
            event_type = EventType.TOOL_COMPLETED if is_tool else EventType.TASK_COMPLETED

            event_logger.log_event(
                run_id=active_run_id,
                event_type=event_type,
                agent_name=agent_name,
                stage=stage,
                output_summary=step_output,
            )
        except Exception as exc:
            logger.error("Error in CrewAI step_callback: %s", exc)

    return _step_callback


def instrument_crewai_tool(
    tool_inst: BaseTool,
    run_id: str | None = None,
    agent_name: str = "Senior Python Engineer",
    stage: str = "code",
    workspace: Path | str | None = None,
) -> BaseTool:
    """Instrument a CrewAI BaseTool instance with logging of TOOL_STARTED and TOOL_COMPLETED."""
    original_run = getattr(tool_inst, "_run", None) or getattr(tool_inst, "run", None)
    if not original_run or getattr(tool_inst, "_is_instrumented_for_logging", False):
        return tool_inst

    tool_name = getattr(tool_inst, "name", type(tool_inst).__name__)

    def _logged_run(*args: Any, **kwargs: Any) -> Any:
        active_run_id = resolve_run_id(run_id)
        active_workspace = resolve_workspace(workspace, run_id=active_run_id)
        event_logger = get_event_logger()

        # Log TOOL_STARTED
        try:
            event_logger.log_event(
                run_id=active_run_id,
                event_type=EventType.TOOL_STARTED,
                agent_name=agent_name,
                stage=stage,
                input_summary={"tool": tool_name, "args": args, "kwargs": kwargs},
            )
        except Exception as exc:
            logger.error("Error logging TOOL_STARTED in CrewAI tool wrapper: %s", exc)

        # Pre-execution tool guardrail check
        from repoagent.security.tool_guardrails import GuardrailViolation, validate_tool_call

        try:
            validate_tool_call(
                tool_name=tool_name,
                args=args,
                kwargs=kwargs,
                workspace=active_workspace,
                run_id=active_run_id,
            )
        except GuardrailViolation:
            raise

        t_start = time.monotonic()
        try:
            result = original_run(*args, **kwargs)
            duration_s = time.monotonic() - t_start

            # Screen tool output before passing back to downstream agent
            text_to_screen = result if isinstance(result, str) else json.dumps(result, default=str)
            from repoagent.security import InputSource, screen_input

            screening = screen_input(
                text_to_screen, source=InputSource.MCP_OUTPUT, run_id=active_run_id
            )
            if screening.is_suspicious and screening.sanitized_content:
                result = screening.sanitized_content

            # Log TOOL_COMPLETED
            try:
                event_logger.log_event(
                    run_id=active_run_id,
                    event_type=EventType.TOOL_COMPLETED,
                    agent_name=agent_name,
                    stage=stage,
                    duration_s=duration_s,
                    output_summary=result,
                )
            except Exception as exc:
                logger.error("Error logging TOOL_COMPLETED in CrewAI tool wrapper: %s", exc)
            return result
        except Exception as exc:
            duration_s = time.monotonic() - t_start
            try:
                event_logger.log_event(
                    run_id=active_run_id,
                    event_type=EventType.ERROR,
                    agent_name=agent_name,
                    stage=stage,
                    duration_s=duration_s,
                    error_details=f"Tool '{tool_name}' failed: {exc}",
                )
            except Exception as log_exc:
                logger.error("Error logging tool error in CrewAI tool wrapper: %s", log_exc)
            raise

    # Replace _run if defined, otherwise replace run
    if hasattr(tool_inst, "_run"):
        tool_inst._run = _logged_run
    else:
        tool_inst.run = _logged_run

    tool_inst._is_instrumented_for_logging = True
    return tool_inst


def instrument_crewai_tools(
    tools: list[BaseTool],
    run_id: str | None = None,
    agent_name: str = "Senior Python Engineer",
    stage: str = "code",
    workspace: Path | str | None = None,
) -> list[BaseTool]:
    """Instrument a list of CrewAI tools with event logging."""
    return [
        instrument_crewai_tool(
            t, run_id=run_id, agent_name=agent_name, stage=stage, workspace=workspace
        )
        for t in tools
    ]
