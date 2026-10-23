# Notes App — Demo Project

A small in-memory Notes API with user accounts, built as a demo project for
**RepoAgent** testing and evaluation.

## Features

- User registration, login, and password change
- CRUD operations for user-owned notes
- In-memory storage (no database required)

## Project Layout

```
demo_repo/
├── src/
│   └── notes_app/
│       ├── models.py   — Pydantic v2 data models (User, Note)
│       ├── auth.py     — AuthService (register, login, change_password)
│       ├── notes.py    — NotesService (CRUD for notes)
│       └── app.py      — NotesApp facade composing the services
└── tests/
    ├── conftest.py     — shared pytest fixtures
    ├── test_auth.py    — authentication tests
    └── test_notes.py   — notes CRUD tests
```

## Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run the test suite
pytest
```
