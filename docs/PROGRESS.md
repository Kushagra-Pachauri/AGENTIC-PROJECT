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

2. **Project configuration & environment setup**:
   - Fixed `build-backend` in `pyproject.toml` to `hatchling.build` (resolving `BackendUnavailable: Cannot import 'hatchling.backends'`).
   - Created dedicated Python 3.11 virtual environment (`.venv` with Python 3.11.15).
   - Resolved cross-framework dependency compatibility between `crewai==1.15.28` and `google-adk==2.12.0`:
     - `google-adk==2.12.0` pins `opentelemetry-sdk<=1.42.1`, `fastapi>=0.133,<1`, and `pydantic>=2.12,<3`.
     - `crewai==1.15.28` pins `mcp>=1.28.1,<1.29`, `python-dotenv>=1.2.2,<2`, and `pydantic<2.13`.
     - Pinned mutually compatible exact versions in `pyproject.toml`:
       `crewai==1.15.28`, `google-adk==2.12.0`, `a2a-sdk==1.2.2`, `mcp[cli]==1.28.1`, `langfuse==4.15.6`, `pydantic==2.12.5`, `fastapi==0.141.1`, `uvicorn[standard]==0.54.0`, `streamlit==1.45.1`, `httpx==0.28.1`, `docker==7.2.0`, `pygithub==2.6.1`, `python-dotenv==1.2.4`.
   - Verified with `pip check`: `No broken requirements found.`
   - Verified with `pip install -e .`: Completed successfully with code 0.
   - `.env.example` with all required environment variable placeholders.
   - `.gitignore` covering `.venv`, `.env`, `runs/`, Python/IDE/OS artifacts.
   - `Makefile` with targets: `install`, `dev`, `test`, `test-demo`, `lint`, `format`, `run`, `clean`.

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

| Test suite | Tests / Check | Status |
|-----------|---------------|--------|
| `pip check` (.venv) | Dependency tree integrity | ✅ Clean (`No broken requirements found.`) |
| `pip install -e .` (.venv) | Editable build & install | ✅ Success (code 0) |
| `tests/test_schemas.py` | 27 unit tests (Python 3.11) | ✅ All passing (0 warnings) |
| `demo_repo/tests/test_auth.py` | 6 unit tests (Python 3.11) | ✅ All passing |
| `demo_repo/tests/test_notes.py` | 6 unit tests (Python 3.11) | ✅ All passing |
| Ruff lint & formatting | 25 files | ✅ Clean (0 issues) |

### Known issues

- None. (`TestReport.__test__ = False` was set to avoid PytestCollectionWarning).

---

*Next step: TBD (awaiting instructions)*
