"""Context management for active run_id and workspace propagation across agents and callbacks."""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

# Context variable storing the active run_id for current execution thread/task
_CURRENT_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "repoagent_current_run_id", default=None
)

# Context variable storing the active workspace path
_CURRENT_WORKSPACE: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "repoagent_current_workspace", default=None
)

# Registry mapping run_id -> canonical workspace Path
_RUN_WORKSPACE_REGISTRY: dict[str, Path] = {}


def get_current_run_id() -> str | None:
    """Retrieve the active run_id from context."""
    return _CURRENT_RUN_ID.get()


def set_current_run_id(run_id: str | None) -> contextvars.Token[str | None]:
    """Set the active run_id in context, returning a token to reset it."""
    clean_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else None
    return _CURRENT_RUN_ID.set(clean_id)


def reset_current_run_id(token: contextvars.Token[str | None]) -> None:
    """Reset the run_id context variable using a previously issued token."""
    _CURRENT_RUN_ID.reset(token)


@contextmanager
def run_id_context(run_id: str | None) -> Generator[str | None, None, None]:
    """Context manager for scoping execution to a specific run_id."""
    token = set_current_run_id(run_id)
    try:
        yield get_current_run_id()
    finally:
        reset_current_run_id(token)


def resolve_run_id(explicit_run_id: str | None = None) -> str:
    """Resolve run_id from explicit argument, context, or fallback with diagnostic."""
    if explicit_run_id and isinstance(explicit_run_id, str) and explicit_run_id.strip():
        return explicit_run_id.strip()

    ctx_id = get_current_run_id()
    if ctx_id:
        return ctx_id

    return "unassociated_run"


def get_current_workspace() -> Path | None:
    """Retrieve the active workspace Path from context."""
    return _CURRENT_WORKSPACE.get()


def set_current_workspace(workspace: Path | str | None) -> contextvars.Token[Path | None]:
    """Set the active workspace in context, returning a token to reset it."""
    ws = Path(workspace).resolve() if workspace else None
    return _CURRENT_WORKSPACE.set(ws)


def reset_current_workspace(token: contextvars.Token[Path | None]) -> None:
    """Reset the workspace context variable using a previously issued token."""
    _CURRENT_WORKSPACE.reset(token)


@contextmanager
def workspace_context(workspace: Path | str | None) -> Generator[Path | None, None, None]:
    """Context manager for scoping execution to an approved workspace."""
    token = set_current_workspace(workspace)
    try:
        yield get_current_workspace()
    finally:
        reset_current_workspace(token)


def register_run_workspace(run_id: str | None, workspace: Path | str | None) -> None:
    """Register the approved workspace path for a specific run_id."""
    if run_id and workspace:
        _RUN_WORKSPACE_REGISTRY[str(run_id).strip()] = Path(workspace).resolve()


def get_workspace_for_run(run_id: str | None) -> Path | None:
    """Lookup registered workspace for a given run_id."""
    if run_id and str(run_id).strip() in _RUN_WORKSPACE_REGISTRY:
        return _RUN_WORKSPACE_REGISTRY[str(run_id).strip()]
    return None


def resolve_workspace(
    explicit_workspace: Path | str | None = None,
    run_id: str | None = None,
) -> Path | None:
    """Resolve workspace from explicit argument, active context, or run_id registry."""
    if explicit_workspace:
        return Path(explicit_workspace).resolve()
    ctx_ws = get_current_workspace()
    if ctx_ws:
        return ctx_ws
    if run_id:
        reg_ws = get_workspace_for_run(run_id)
        if reg_ws:
            return reg_ws
    return None
