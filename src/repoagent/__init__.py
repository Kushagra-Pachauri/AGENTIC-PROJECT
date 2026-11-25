"""RepoAgent - Agentic AI system for GitHub PRs."""

from __future__ import annotations

import contextlib
import os
import pathlib
import sys

# Ensure UTF-8 output encoding across Windows consoles for CrewAI events
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stderr.reconfigure(encoding="utf-8")

# Ensure CrewAI credential and settings paths stay hermetic within workspace
_storage = pathlib.Path.cwd() / ".crewai_storage"
_storage.mkdir(exist_ok=True)
_localappdata = _storage / "localappdata"
_localappdata.mkdir(exist_ok=True)
os.environ["LOCALAPPDATA"] = str(_localappdata)
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
if os.environ.get("LANGFUSE_ENABLED", "").lower() not in ("true", "1", "yes"):
    os.environ["OTEL_SDK_DISABLED"] = "true"

with contextlib.suppress(Exception):
    from crewai_core import paths, settings, token_manager

    def _safe_storage_path() -> pathlib.Path:
        p = _storage / "credentials"
        p.mkdir(parents=True, exist_ok=True)
        return p

    token_manager.TokenManager._get_secure_storage_path = staticmethod(_safe_storage_path)
    settings.get_writable_config_path = lambda: _storage / "settings.json"
    paths.db_storage_path = lambda: str(_storage / "db")

    from crewai.events.event_bus import crewai_event_bus

    crewai_event_bus._ensure_executor_initialized = lambda: None
    crewai_event_bus.emit = lambda *args, **kwargs: None
