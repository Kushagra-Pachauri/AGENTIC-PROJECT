"""Pytest root configuration: isolates CrewAI credential, settings, and db paths."""

from __future__ import annotations

import os
import pathlib

_test_storage = pathlib.Path(__file__).parent.parent / ".pytest_crewai"
_test_storage.mkdir(exist_ok=True)

# Ensure environment fallbacks point to workspace-local directory before importing crewai
_localappdata = _test_storage / "localappdata"
_localappdata.mkdir(exist_ok=True)
os.environ["LOCALAPPDATA"] = str(_localappdata)
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
os.environ["OTEL_SDK_DISABLED"] = "true"

from crewai_core import paths, settings, token_manager  # noqa: E402


def _safe_storage_path() -> pathlib.Path:
    p = _test_storage / "credentials"
    p.mkdir(parents=True, exist_ok=True)
    return p


token_manager.TokenManager._get_secure_storage_path = staticmethod(_safe_storage_path)
settings.get_writable_config_path = lambda: _test_storage / "settings.json"
paths.db_storage_path = lambda: str(_test_storage / "db")

from unittest.mock import MagicMock  # noqa: E402

import crewai.memory.storage.lancedb_storage as lancedb_storage  # noqa: E402
import crewai.memory.unified_memory as unified_memory  # noqa: E402


class _DummyStorage:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return MagicMock()


lancedb_storage.LanceDBStorage = _DummyStorage
unified_memory.LanceDBStorage = _DummyStorage
