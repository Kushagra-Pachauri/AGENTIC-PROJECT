"""Google ADK callback integration for RepoAgent centralized event logging.

Captures agent, tool, and model lifecycle events for Google ADK agents
(such as the Planner and Code Reviewer) and records them to `runs/<run_id>/events.jsonl`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from repoagent.monitoring.context import resolve_run_id
from repoagent.monitoring.event_logger import EventType, get_event_logger

if TYPE_CHECKING:
    from google.adk.agents import Agent
    from google.adk.agents.context import Context
    from google.adk.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


def _extract_context_run_id(ctx: Context, explicit_run_id: str | None = None) -> str:
    """Safely extract run_id from ADK Context, fallback, or diagnostic."""
    if explicit_run_id and str(explicit_run_id).strip():
        return str(explicit_run_id).strip()

    ctx_run_id = getattr(ctx, "run_id", None)
    if ctx_run_id and str(ctx_run_id).strip():
        return str(ctx_run_id).strip()

    return resolve_run_id(None)


def create_adk_before_agent_callback(
    explicit_run_id: str | None = None,
    default_agent_name: str = "adk_agent",
    stage: str = "adk",
) -> Any:
    """Build callback invoked immediately prior to ADK Agent execution."""

    def _before_agent(ctx: Context) -> None:
        try:
            run_id = _extract_context_run_id(ctx, explicit_run_id)
            agent_name = getattr(ctx, "agent_name", None) or default_agent_name

            input_summary: Any = getattr(ctx, "user_content", None)
            if not input_summary and hasattr(ctx, "invocation_context"):
                inv_ctx = getattr(ctx, "invocation_context", None)
                if inv_ctx and hasattr(inv_ctx, "user_content"):
                    input_summary = inv_ctx.user_content

            if input_summary and isinstance(input_summary, str):
                from repoagent.security import GuardrailDecision, InputSource, screen_input

                screening = screen_input(input_summary, source=InputSource.A2A_MESSAGE, run_id=None)
                if screening.decision in (GuardrailDecision.BLOCK, GuardrailDecision.FLAG):
                    from repoagent.security.input_guardrails import _log_screening

                    _log_screening(run_id, screening)
                    raise PermissionError(
                        f"Inbound execution rejected by ADK agent guardrail: {screening.reason}"
                    )

            get_event_logger().log_event(
                run_id=run_id,
                event_type=EventType.AGENT_STARTED,
                agent_name=agent_name,
                stage=stage,
                input_summary=input_summary or f"{agent_name} invocation started",
                metadata={"framework": "Google ADK"},
            )
        except PermissionError:
            raise
        except Exception as exc:
            logger.error("Error in ADK before_agent_callback: %s", exc)
        return None

    return _before_agent


def create_adk_after_agent_callback(
    explicit_run_id: str | None = None,
    default_agent_name: str = "adk_agent",
    stage: str = "adk",
) -> Any:
    """Build callback invoked immediately following ADK Agent execution."""

    def _after_agent(ctx: Context) -> None:
        try:
            run_id = _extract_context_run_id(ctx, explicit_run_id)
            agent_name = getattr(ctx, "agent_name", None) or default_agent_name

            output_summary = getattr(ctx, "output", None)

            get_event_logger().log_event(
                run_id=run_id,
                event_type=EventType.AGENT_COMPLETED,
                agent_name=agent_name,
                stage=stage,
                output_summary=output_summary or f"{agent_name} invocation completed",
                metadata={"framework": "Google ADK"},
            )
        except Exception as exc:
            logger.error("Error in ADK after_agent_callback: %s", exc)
        return None

    return _after_agent


def create_adk_before_tool_callback(
    explicit_run_id: str | None = None,
    default_agent_name: str = "adk_agent",
    stage: str = "adk",
) -> Any:
    """Build callback invoked prior to an ADK tool execution."""

    def _before_tool(tool: BaseTool, args: dict[str, Any], ctx: Context) -> None:
        try:
            run_id = _extract_context_run_id(ctx, explicit_run_id)
            agent_name = getattr(ctx, "agent_name", None) or default_agent_name
            tool_name = getattr(tool, "name", type(tool).__name__)

            get_event_logger().log_event(
                run_id=run_id,
                event_type=EventType.TOOL_STARTED,
                agent_name=agent_name,
                stage=stage,
                input_summary={"tool": tool_name, "arguments": args},
                metadata={"framework": "Google ADK"},
            )
        except Exception as exc:
            logger.error("Error in ADK before_tool_callback: %s", exc)
        return None

    return _before_tool


def create_adk_after_tool_callback(
    explicit_run_id: str | None = None,
    default_agent_name: str = "adk_agent",
    stage: str = "adk",
) -> Any:
    """Build callback invoked following an ADK tool execution."""

    def _after_tool(
        tool: BaseTool, args: dict[str, Any], ctx: Context, result: dict[str, Any]
    ) -> None:
        try:
            run_id = _extract_context_run_id(ctx, explicit_run_id)
            agent_name = getattr(ctx, "agent_name", None) or default_agent_name
            tool_name = getattr(tool, "name", type(tool).__name__)

            get_event_logger().log_event(
                run_id=run_id,
                event_type=EventType.TOOL_COMPLETED,
                agent_name=agent_name,
                stage=stage,
                output_summary={"tool": tool_name, "result": result},
                metadata={"framework": "Google ADK"},
            )
        except Exception as exc:
            logger.error("Error in ADK after_tool_callback: %s", exc)
        return None

    return _after_tool


def create_adk_on_tool_error_callback(
    explicit_run_id: str | None = None,
    default_agent_name: str = "adk_agent",
    stage: str = "adk",
) -> Any:
    """Build callback invoked when an ADK tool raises an exception."""

    def _on_tool_error(
        tool: BaseTool, args: dict[str, Any], ctx: Context, error: Exception
    ) -> None:
        try:
            run_id = _extract_context_run_id(ctx, explicit_run_id)
            agent_name = getattr(ctx, "agent_name", None) or default_agent_name
            tool_name = getattr(tool, "name", type(tool).__name__)

            get_event_logger().log_event(
                run_id=run_id,
                event_type=EventType.ERROR,
                agent_name=agent_name,
                stage=stage,
                error_details=f"Tool '{tool_name}' failed: {error}",
                metadata={"framework": "Google ADK"},
            )
        except Exception as exc:
            logger.error("Error in ADK on_tool_error_callback: %s", exc)
        return None

    return _on_tool_error


def attach_adk_callbacks(
    agent: Agent,
    run_id: str | None = None,
    agent_name: str | None = None,
    stage: str | None = None,
) -> Agent:
    """Attach structured event logging callbacks to a Google ADK Agent."""
    eff_agent_name = agent_name or getattr(agent, "name", "adk_agent")
    eff_stage = stage or ("analyze" if "planner" in eff_agent_name else "review")

    agent.before_agent_callback = create_adk_before_agent_callback(
        explicit_run_id=run_id, default_agent_name=eff_agent_name, stage=eff_stage
    )
    agent.after_agent_callback = create_adk_after_agent_callback(
        explicit_run_id=run_id, default_agent_name=eff_agent_name, stage=eff_stage
    )
    agent.before_tool_callback = create_adk_before_tool_callback(
        explicit_run_id=run_id, default_agent_name=eff_agent_name, stage=eff_stage
    )
    agent.after_tool_callback = create_adk_after_tool_callback(
        explicit_run_id=run_id, default_agent_name=eff_agent_name, stage=eff_stage
    )
    agent.on_tool_error_callback = create_adk_on_tool_error_callback(
        explicit_run_id=run_id, default_agent_name=eff_agent_name, stage=eff_stage
    )
    return agent
