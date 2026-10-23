"""Authentication service with in-memory user store."""

from __future__ import annotations

import hashlib

from notes_app.models import User


def _hash_password(password: str) -> str:
    """Hash a password using SHA-256 (demo only — not production-safe)."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


class AuthService:
    """Handles user registration, login, and password management.

    Uses an in-memory dictionary keyed by username.
    """

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    def register(self, username: str, email: str, password: str) -> User:
        """Register a new user.

        Parameters
        ----------
        username:
            Unique username.
        email:
            User's e-mail address.
        password:
            Plain-text password (will be hashed).

        Returns
        -------
        User
            The newly created user.

        Raises
        ------
        ValueError
            If *username* is already taken.
        """
        if username in self._users:
            raise ValueError(f"Username '{username}' is already taken")
        user = User(
            username=username,
            email=email,
            hashed_password=_hash_password(password),
        )
        self._users[username] = user
        return user

    def login(self, username: str, password: str) -> User:
        """Authenticate a user.

        Returns
        -------
        User
            The authenticated user.

        Raises
        ------
        ValueError
            If the username does not exist or the password is wrong.
        """
        user = self._users.get(username)
        if user is None or user.hashed_password != _hash_password(password):
            raise ValueError("Invalid username or password")
        return user

    def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        """Change a user's password after verifying the old one.

        Returns
        -------
        bool
            ``True`` on success.

        Raises
        ------
        ValueError
            If the username does not exist or *old_password* is wrong.
        """
        user = self._users.get(username)
        if user is None or user.hashed_password != _hash_password(old_password):
            raise ValueError("Invalid username or password")
        user.hashed_password = _hash_password(new_password)
        return True

    def get_user(self, username: str) -> User | None:
        """Return the user for *username*, or ``None`` if not found."""
        return self._users.get(username)
