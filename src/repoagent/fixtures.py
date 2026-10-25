"""Hardcoded fixtures for RepoAgent development and testing."""

from __future__ import annotations

from repoagent.schemas import ChangeSet, FileChange, Plan, PlannedFile

# Hardcoded Plan fixture for Step 4 requirement: 'Add password-reset functionality' on demo_repo
PASSWORD_RESET_PLAN = Plan(
    summary="Add password-reset functionality to the Notes application",
    files_to_change=[
        PlannedFile(
            path="src/notes_app/auth.py",
            reason="Implement reset token generation and verification in AuthService",
        ),
        PlannedFile(
            path="src/notes_app/app.py",
            reason="Expose request_password_reset and reset_password methods on NotesApp",
        ),
        PlannedFile(
            path="tests/test_auth.py",
            reason="Add unit tests verifying token issuance, password reset, and error cases",
        ),
    ],
    steps=[
        (
            "1. In src/notes_app/auth.py: Add a reset_tokens in-memory store. "
            "Add request_password_reset(username) returning a unique token. "
            "Add reset_password(username, token, new_password) to update password."
        ),
        (
            "2. In src/notes_app/app.py: Add request_password_reset(username) "
            "and reset_password(username, token, new_password) delegates."
        ),
        (
            "3. In tests/test_auth.py: Add test_request_password_reset, "
            "test_reset_password_success, and test_reset_password_invalid_token."
        ),
        ("4. Verify all tests pass including the existing 12 test cases and linting passes."),
    ],
    risks=[
        "Reset tokens must be single-use and associated with the requested username.",
        "Existing auth methods (register, login, change_password) must not break.",
    ],
    test_strategy="Execute pytest suite in Docker sandbox to verify 100% pass rate.",
)

_AUTH_DIFF = (
    "@@ -3,6 +3,7 @@\n"
    " from __future__ import annotations\n"
    "\n"
    " import hashlib\n"
    "+import uuid\n"
    "\n"
    " from notes_app.models import User\n"
    "@@ -21,4 +22,5 @@\n"
    "     def __init__(self) -> None:\n"
    "         self._users: dict[str, User] = {}\n"
    "+        self._reset_tokens: dict[str, str] = {}\n"
    "@@ -93,3 +95,19 @@\n"
    "     def get_user(self, username: str) -> User | None:\n"
    '         """Return the user for *username*, or ``None`` if not found."""\n'
    "         return self._users.get(username)\n"
    "+\n"
    "    def request_password_reset(self, username: str) -> str:\n"
    '        """Request a password reset token for a registered user."""\n'
    "        if username not in self._users:\n"
    "            raise ValueError(f\"User '{username}' not found\")\n"
    "        token = uuid.uuid4().hex\n"
    "        self._reset_tokens[token] = username\n"
    "        return token\n"
    "+\n"
    "    def reset_password(self, username: str, token: str, new_password: str) -> bool:\n"
    '        """Reset a user\'s password using a reset token."""\n'
    "        if username not in self._users:\n"
    "            raise ValueError(f\"User '{username}' not found\")\n"
    "        if self._reset_tokens.get(token) != username:\n"
    '            raise ValueError("Invalid or expired reset token")\n'
    "        self._users[username].hashed_password = _hash_password(new_password)\n"
    "        del self._reset_tokens[token]\n"
    "        return True\n"
)

_APP_DIFF = (
    "@@ -62,3 +62,11 @@\n"
    "         if note.user_id != user.id:\n"
    '             raise PermissionError("You can only delete your own notes")\n'
    "         return self.notes.delete_note(note_id)\n"
    "+\n"
    "    def request_password_reset(self, username: str) -> str:\n"
    '        """Request a password reset token for username."""\n'
    "        return self.auth.request_password_reset(username)\n"
    "+\n"
    "    def reset_password(self, username: str, token: str, new_password: str) -> bool:\n"
    '        """Reset password using reset token."""\n'
    "        return self.auth.reset_password(username, token, new_password)\n"
)

_TEST_DIFF = (
    "@@ -51,3 +51,26 @@\n"
    "         # New password should succeed\n"
    '         user = app_with_user.login("alice", "n3wpass")\n'
    '         assert user.username == "alice"\n'
    "+\n"
    "    def test_request_password_reset(self, app_with_user: NotesApp) -> None:\n"
    '        """Requesting password reset returns a valid non-empty token."""\n'
    '        token = app_with_user.request_password_reset("alice")\n'
    "        assert bool(token)\n"
    '        with pytest.raises(ValueError, match="not found"):\n'
    '            app_with_user.request_password_reset("nonexistent")\n'
    "+\n"
    "    def test_reset_password_success(self, app_with_user: NotesApp) -> None:\n"
    '        """Resetting password updates password and invalidates token."""\n'
    '        token = app_with_user.request_password_reset("alice")\n'
    '        res = app_with_user.reset_password("alice", token, "brand_new_pw")\n'
    "        assert res is True\n"
    '        user = app_with_user.login("alice", "brand_new_pw")\n'
    '        assert user.username == "alice"\n'
    '        with pytest.raises(ValueError, match="Invalid"):\n'
    '            app_with_user.reset_password("alice", token, "another_pw")\n'
    "+\n"
    "    def test_reset_password_invalid_token(self, app_with_user: NotesApp) -> None:\n"
    '        """Invalid token raises ValueError."""\n'
    '        app_with_user.request_password_reset("alice")\n'
    '        with pytest.raises(ValueError, match="Invalid"):\n'
    '            app_with_user.reset_password("alice", "wrong_token", "pw")\n'
)

# Pre-recorded known-good ChangeSet for REPLAY mode verification
KNOWN_GOOD_PASSWORD_RESET_CHANGESET = ChangeSet(
    files=[
        FileChange(path="src/notes_app/auth.py", action="modify", diff=_AUTH_DIFF),
        FileChange(path="src/notes_app/app.py", action="modify", diff=_APP_DIFF),
        FileChange(path="tests/test_auth.py", action="modify", diff=_TEST_DIFF),
    ],
    summary="Known-good password reset implementation for REPLAY verification",
)
