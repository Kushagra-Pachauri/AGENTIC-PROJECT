# RepoAgent — Progress Log

## Step 1: Project Setup and Design Freeze ✅

**Date:** 2026-10-23
**Status:** Complete

### What was done

1. **Directory layout** — Full project scaffold created:
   - `src/repoagent/` with all subpackages: `providers`, `agents/crewai`,
     `agents/adk`, `a2a`, `mcp_servers`, `guardrails`, `callbacks`,
     `observability`, `api`, `ui`
   - Supporting directories: `sandbox/`, `demo_repo/`, `eval/`, `scripts/`,
     `docs/`, `tests/`

2. **Project configuration** — Created:
   - `pyproject.toml` with pinned dependencies (CrewAI 1.15.28, ADK 2.12.0,
     A2A SDK 1.2.2, MCP 2.3.0, Langfuse 5.0.2, Pydantic 2.11.7, etc.)
   - `.env.example` with all required environment variable placeholders
   - `.gitignore` covering `.env`, `runs/`, Python/IDE/OS artifacts
   - `Makefile` with targets: `install`, `dev`, `test`, `test-demo`, `lint`,
     `format`, `run`, `clean`

3. **Pydantic v2 schemas** (`src/repoagent/schemas.py`) — 12 models:
   - Enums: `RunStatus`, `ReviewVerdict`
   - Value objects (frozen): `FileChange`, `PlannedFile`, `ReviewFinding`
   - Domain models: `Plan`, `ChangeSet`, `TestReport`, `ReviewReport`, `PRSummary`
   - Central state: `RunState` with `validate_assignment=True`
   - 26 unit tests in `tests/test_schemas.py` — all passing

4. **Demo repository** (`demo_repo/`) — Notes API with user accounts:
   - `notes_app` package: `models.py`, `auth.py`, `notes.py`, `app.py`
   - Features: user registration, login, change password, notes CRUD
   - Deliberately **no** password-reset feature (realistic future task)
   - 12 pytest tests — all passing

5. **Architecture documentation** (`docs/ARCHITECTURE.md`):
   - Mermaid diagrams: system overview + data flow
   - Agent responsibilities table
   - MCP tool servers table
   - 11-row evaluation criteria mapping table
   - Security invariants section
   - Technology stack with pinned versions

### How to run

```bash
# Install the project in dev mode
pip install -e ".[dev]"

# Run schema tests (26 tests)
pytest tests/ -v

# Run demo repo tests (12 tests)
pytest demo_repo/tests/ -v

# Lint and format checks
ruff check src/ tests/ demo_repo/
ruff format --check src/ tests/ demo_repo/
```

### Test results

| Test suite | Tests | Status |
|-----------|-------|--------|
| `tests/test_schemas.py` | 26 | ✅ All passing (0 warnings) |
| `demo_repo/tests/test_auth.py` | 6 | ✅ All passing |
| `demo_repo/tests/test_notes.py` | 6 | ✅ All passing |
| Ruff lint & formatting | 25 files | ✅ Clean (0 issues) |

### Known issues

- None. (`TestReport.__test__ = False` was set to avoid PytestCollectionWarning).

---

*Next step: TBD (awaiting instructions)*
