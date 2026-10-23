"""Application façade that composes AuthService and NotesService."""

from __future__ import annotations

from notes_app.auth import AuthService
from notes_app.models import Note, User
from notes_app.notes import NotesService


class NotesApp:
    """High-level API that ties authentication and notes together.

    Every mutating notes operation requires a valid username so that
    ownership can be enforced.
    """

    def __init__(self) -> None:
        self.auth = AuthService()
        self.notes = NotesService()

    # -- Auth helpers -------------------------------------------------------

    def register_user(self, username: str, email: str, password: str) -> User:
        """Register a new user account."""
        return self.auth.register(username, email, password)

    def login(self, username: str, password: str) -> User:
        """Authenticate and return the user."""
        return self.auth.login(username, password)

    # -- Notes --------------------------------------------------------------

    def _require_user(self, username: str) -> User:
        """Return the user or raise ``ValueError``."""
        user = self.auth.get_user(username)
        if user is None:
            raise ValueError(f"User '{username}' not found")
        return user

    def create_note(self, username: str, title: str, content: str) -> Note:
        """Create a note for *username* (must be a registered user)."""
        user = self._require_user(username)
        return self.notes.create_note(user.id, title, content)

    def list_user_notes(self, username: str) -> list[Note]:
        """Return all notes belonging to *username*."""
        user = self._require_user(username)
        return self.notes.list_notes(user.id)

    def delete_note(self, username: str, note_id: str) -> bool:
        """Delete a note — only the owner may do so.

        Raises
        ------
        PermissionError
            If the note exists but does not belong to *username*.
        """
        user = self._require_user(username)
        note = self.notes.get_note(note_id)
        if note is None:
            return False
        if note.user_id != user.id:
            raise PermissionError("You can only delete your own notes")
        return self.notes.delete_note(note_id)
