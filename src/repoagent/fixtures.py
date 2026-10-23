"""Hardcoded fixtures for RepoAgent development and testing."""

from __future__ import annotations

from repoagent.schemas import Plan, PlannedFile

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
