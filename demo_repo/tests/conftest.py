"""Shared pytest fixtures for the Notes App test suite."""

from __future__ import annotations

import pytest
from notes_app.app import NotesApp


@pytest.fixture()
def app() -> NotesApp:
    """Return a fresh :class:`NotesApp` instance."""
    return NotesApp()


@pytest.fixture()
def app_with_user(app: NotesApp) -> NotesApp:
    """Return a :class:`NotesApp` with a pre-registered user ``alice``."""
    app.register_user("alice", "alice@example.com", "s3cret")
    return app
