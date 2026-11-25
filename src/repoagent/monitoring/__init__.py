"""Monitoring, event logging, and callback integration for RepoAgent."""

from repoagent.monitoring.adk_callbacks import (
    attach_adk_callbacks,
    create_adk_after_agent_callback,
    create_adk_after_tool_callback,
    create_adk_before_agent_callback,
    create_adk_before_tool_callback,
    create_adk_on_tool_error_callback,
)
from repoagent.monitoring.context import (
    get_current_run_id,
    get_current_workspace,
    get_workspace_for_run,
    register_run_workspace,
    reset_current_run_id,
    reset_current_workspace,
    resolve_run_id,
    resolve_workspace,
    run_id_context,
    set_current_run_id,
    set_current_workspace,
    workspace_context,
)
from repoagent.monitoring.crewai_callbacks import (
    create_crewai_after_kickoff,
    create_crewai_before_kickoff,
    create_crewai_step_callback,
    create_crewai_task_callback,
    instrument_crewai_tool,
    instrument_crewai_tools,
)
from repoagent.monitoring.event_logger import Event, EventLogger, EventType, get_event_logger

__all__ = [
    "Event",
    "EventLogger",
    "EventType",
    "attach_adk_callbacks",
    "create_adk_after_agent_callback",
    "create_adk_after_tool_callback",
    "create_adk_before_agent_callback",
    "create_adk_before_tool_callback",
    "create_adk_on_tool_error_callback",
    "create_crewai_after_kickoff",
    "create_crewai_before_kickoff",
    "create_crewai_step_callback",
    "create_crewai_task_callback",
    "get_current_run_id",
    "get_current_workspace",
    "get_event_logger",
    "get_workspace_for_run",
    "instrument_crewai_tool",
    "instrument_crewai_tools",
    "register_run_workspace",
    "reset_current_run_id",
    "reset_current_workspace",
    "resolve_run_id",
    "resolve_workspace",
    "run_id_context",
    "set_current_run_id",
    "set_current_workspace",
    "workspace_context",
]
