"""Tests for the authentication service."""

from __future__ import annotations

import pytest
from notes_app.app import NotesApp


class TestAuth:
    """Authentication-related tests."""

    def test_register_user(self, app: NotesApp) -> None:
        """Successful registration returns a ``User`` with correct fields."""
        user = app.register_user("bob", "bob@example.com", "pw123")
        assert user.username == "bob"
        assert user.email == "bob@example.com"
        # Password must be stored as a hash, not plain text
        assert user.hashed_password != "pw123"
        assert user.id  # non-empty UUID

    def test_register_duplicate_username(self, app: NotesApp) -> None:
        """Registering the same username twice raises ``ValueError``."""
        app.register_user("bob", "bob@example.com", "pw123")
        with pytest.raises(ValueError, match="already taken"):
            app.register_user("bob", "bob2@example.com", "other")

    def test_login_success(self, app_with_user: NotesApp) -> None:
        """Valid credentials return the corresponding user."""
        user = app_with_user.login("alice", "s3cret")
        assert user.username == "alice"

    def test_login_wrong_password(self, app_with_user: NotesApp) -> None:
        """Wrong password raises ``ValueError``."""
        with pytest.raises(ValueError, match="Invalid"):
            app_with_user.login("alice", "wrong")

    def test_login_nonexistent_user(self, app: NotesApp) -> None:
        """Logging in with a username that doesn't exist raises ``ValueError``."""
        with pytest.raises(ValueError, match="Invalid"):
            app.login("ghost", "nope")

    def test_change_password(self, app_with_user: NotesApp) -> None:
        """After changing password the old one is rejected and the new one works."""
        result = app_with_user.auth.change_password("alice", "s3cret", "n3wpass")
        assert result is True

        # Old password should fail
        with pytest.raises(ValueError):
            app_with_user.login("alice", "s3cret")

        # New password should succeed
        user = app_with_user.login("alice", "n3wpass")
        assert user.username == "alice"
