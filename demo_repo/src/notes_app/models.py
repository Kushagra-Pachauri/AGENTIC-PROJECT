"""Data models for the Notes App."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def _new_id() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


class User(BaseModel):
    """A registered user account."""

    id: str = Field(default_factory=_new_id)
    username: str
    email: str
    hashed_password: str
    created_at: datetime = Field(default_factory=_utc_now)


class Note(BaseModel):
    """A user-owned note."""

    id: str = Field(default_factory=_new_id)
    user_id: str
    title: str
    content: str
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime | None = None
