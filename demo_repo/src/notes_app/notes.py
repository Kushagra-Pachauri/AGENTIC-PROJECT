"""CRUD service for notes with in-memory storage."""

from __future__ import annotations

from datetime import UTC, datetime

from notes_app.models import Note


class NotesService:
    """Manages note lifecycle — create, read, update, delete.

    Notes are stored in an in-memory dictionary keyed by note id.
    """

    def __init__(self) -> None:
        self._notes: dict[str, Note] = {}

    def create_note(self, user_id: str, title: str, content: str) -> Note:
        """Create a new note owned by *user_id*."""
        note = Note(user_id=user_id, title=title, content=content)
        self._notes[note.id] = note
        return note

    def get_note(self, note_id: str) -> Note | None:
        """Return the note with *note_id*, or ``None`` if not found."""
        return self._notes.get(note_id)

    def list_notes(self, user_id: str) -> list[Note]:
        """Return all notes belonging to *user_id*."""
        return [n for n in self._notes.values() if n.user_id == user_id]

    def update_note(
        self,
        note_id: str,
        title: str | None = None,
        content: str | None = None,
    ) -> Note:
        """Update the title and/or content of an existing note.

        Raises
        ------
        KeyError
            If *note_id* does not exist.
        """
        note = self._notes.get(note_id)
        if note is None:
            raise KeyError(f"Note '{note_id}' not found")
        if title is not None:
            note.title = title
        if content is not None:
            note.content = content
        note.updated_at = datetime.now(UTC)
        return note

    def delete_note(self, note_id: str) -> bool:
        """Delete a note by id.

        Returns
        -------
        bool
            ``True`` if the note existed and was deleted, ``False`` otherwise.
        """
        if note_id in self._notes:
            del self._notes[note_id]
            return True
        return False
