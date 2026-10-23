"""Tests for the notes CRUD service."""

from __future__ import annotations

import pytest
from notes_app.app import NotesApp


class TestNotes:
    """Notes CRUD tests."""

    def test_create_note(self, app_with_user: NotesApp) -> None:
        """Creating a note for a valid user returns a ``Note`` with correct data."""
        note = app_with_user.create_note("alice", "Shopping", "Buy milk")
        assert note.title == "Shopping"
        assert note.content == "Buy milk"
        assert note.id  # non-empty UUID

    def test_list_notes(self, app_with_user: NotesApp) -> None:
        """Listing returns only the notes for the specified user."""
        app_with_user.create_note("alice", "Note A", "AAA")
        app_with_user.create_note("alice", "Note B", "BBB")

        # Register a second user and add a note
        app_with_user.register_user("bob", "bob@example.com", "pw")
        app_with_user.create_note("bob", "Note C", "CCC")

        alice_notes = app_with_user.list_user_notes("alice")
        assert len(alice_notes) == 2
        titles = {n.title for n in alice_notes}
        assert titles == {"Note A", "Note B"}

    def test_update_note(self, app_with_user: NotesApp) -> None:
        """Updating a note changes its title/content and sets ``updated_at``."""
        note = app_with_user.create_note("alice", "Old", "old content")
        updated = app_with_user.notes.update_note(note.id, title="New", content="new content")
        assert updated.title == "New"
        assert updated.content == "new content"
        assert updated.updated_at is not None

    def test_delete_note(self, app_with_user: NotesApp) -> None:
        """Owner can successfully delete their own note."""
        note = app_with_user.create_note("alice", "Temp", "delete me")
        result = app_with_user.delete_note("alice", note.id)
        assert result is True
        # Verify it's gone
        assert app_with_user.notes.get_note(note.id) is None

    def test_delete_note_not_found(self, app_with_user: NotesApp) -> None:
        """Deleting a nonexistent note returns ``False``."""
        result = app_with_user.delete_note("alice", "nonexistent-id")
        assert result is False

    def test_update_note_not_found(self, app_with_user: NotesApp) -> None:
        """Updating a nonexistent note raises ``KeyError``."""
        with pytest.raises(KeyError):
            app_with_user.notes.update_note("no-such-id", title="X")
