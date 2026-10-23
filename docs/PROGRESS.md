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

## Step 2: Orchestration Skeleton with Shared State and Provider Adapter ✅

**Date:** 2026-10-24
**Status:** Complete

### What was done

1. **LLM Provider Adapter (`src/repoagent/providers/`)**:
   - `LLMProvider` abstract base interface defining `get_crewai_llm()` and `get_adk_model()`.
   - `GeminiProvider`: hosted provider adapter for Google Gemini (`gemini-2.0-flash`), producing `crewai.LLM` and Google ADK model identifier.
   - `OpenAICompatibleProvider`: local/self-hosted provider adapter for coding models (e.g., `qwen2.5-coder`, `deepseek-coder` via Ollama/vLLM) with configurable `base_url` and `api_key`.
   - `get_provider()` factory selecting provider via the `REPOAGENT_PROVIDER` environment variable (default: `gemini`).
   - Smoke test script at `scripts/smoke_test_provider.py`.

2. **CrewAI Flow Orchestrator (`src/repoagent/flow.py`)**:
   - Implemented `RepoAgentFlow(Flow[RunState])` managing the pipeline:
     `analyze` -> `plan` -> `code` -> `test` -> [router: `review` -> `open_pr` | `retry` -> `code` | `needs_human`].
   - Configured routing after `test`:
     - Test passes -> proceeds to `review` and then `open_pr` (setting status to `done`).
     - Test fails with remaining budget -> increments `retry_count` and loops back to `code`.
     - Test fails with budget exhausted (`retry_count >= max_retries`) -> terminates with status `needs_human` and records error explanation.
   - Preserves security invariants: strictly operates on branches named `repoagent/<run_id>`, never touches main.

3. **CLI Entry Point (`src/repoagent/run.py`)**:
   - Implemented `python -m repoagent.run --repo <path> --requirement '<text>'`.
   - Added optional flags: `--provider`, `--max-retries`, `--simulate-failures`.
   - Prints the final `RunState` as formatted JSON to stdout.

4. **Automated Tests**:
   - `tests/test_flow.py`:
     - `test_flow_completes_end_to_end`: verifies full happy path execution.
     - `test_retry_cap_path_ends_with_needs_human`: verifies capped retry loop and `needs_human` termination.
     - `test_state_sharing_between_steps`: verifies data passed across all stages via shared `RunState`.
     - `test_branch_naming_invariant`: verifies branch naming constraint `repoagent/<run_id>`.
     - `test_cli_execution`: verifies CLI main function invocation and JSON output.
   - `tests/test_providers.py`:
     - Tests defaults, custom configurations, and factory selection for both providers.

### How to run

```bash
# Run provider smoke test
python scripts/smoke_test_provider.py

# Run the CLI on demo_repo
python -m repoagent.run --repo demo_repo --requirement "Add password-reset functionality"

# Run all test suites (39 tests)
pytest tests/ -v

# Run demo_repo tests (12 tests)
pytest demo_repo/tests/ -v

# Lint and format checks
ruff check src/ tests/ scripts/ demo_repo/
ruff format --check src/ tests/ scripts/ demo_repo/
```

### Test results

| Test suite | Tests / Check | Status |
|-----------|---------------|--------|
| `tests/test_flow.py` | 5 unit tests | ✅ All passing |
| `tests/test_providers.py` | 7 unit tests | ✅ All passing |
| `tests/test_schemas.py` | 27 unit tests | ✅ All passing |
| `demo_repo/tests/` | 12 unit tests | ✅ All passing |
| Total Tests | 51 tests across repo | ✅ 51 passed |
| Ruff lint & formatting | 33 files | ✅ Clean (0 issues) |

### Known issues

- None.

---

## Step 3: MCP Tool Layer ✅

**Date:** 2026-10-24
**Status:** Complete

### What was done

1. **GitHub MCP Server Integration (`src/repoagent/mcp_client.py`)**:
   - Integrated with official reference MCP server `@modelcontextprotocol/server-github` via stdio transport.
   - Configured `get_github_params()` fetching credentials strictly from environment (`GITHUB_TOKEN` or `GITHUB_PERSONAL_ACCESS_TOKEN`).
   - Cross-platform executable resolution via `shutil.which("npx")` to handle Windows `.CMD` shims as well as POSIX paths.

2. **Repo Reader MCP Server (`src/repoagent/mcp_servers/repo_reader.py`)**:
   - Implemented custom FastMCP server restricted to a configured workspace directory (`REPOAGENT_WORKSPACE`).
   - Tools exposed:
     - `list_tree(path, max_depth)`: recursive directory hierarchy with configurable depth limit, auto-filtering noise dirs (`.git`, `__pycache__`, `.venv`, `.pytest_cache`, `.ruff_cache`).
     - `read_file(path, max_lines)`: utf-8 text file reader with line truncation safety.
     - `search_code(pattern, path, max_results)`: regex-based code search returning `path:line_no: content`.
   - Security invariants strictly enforced in `_resolve_safe`:
     - Blocks relative path traversal escaping the workspace root (`../../`).
     - Blocks absolute POSIX paths (`/etc/passwd`), Windows drive paths (`C:/Windows`), and UNC shares (`\\server\share`).

3. **Test Runner Docker Sandbox MCP Server (`src/repoagent/mcp_servers/test_runner.py`)**:
   - Built isolated container environment (`sandbox/Dockerfile`):
     - Based on `python:3.11-slim`, non-root user `sandbox`.
     - Pre-installed with `pytest==8.4.1`, `ruff==0.11.13`, `pydantic==2.12.5`, Node.js, and npm.
     - Network disabled (`network_mode="none"`).
     - Strict resource limits enforced: nano-CPUs (default 1.0), memory limit (default 512m), execution timeout (default 120s).
   - Tools exposed:
     - `run_tests(command)`: runs validated test suite commands, returning a structured `TestReport` model.
     - `run_lint()`: runs `ruff check .` inside the sandbox, returning a structured `TestReport` model.
   - Command security validation (`_validate_command`):
     - Allowlisted commands strictly limited to: `pytest`, `ruff`, `npm test`, `eslint`.
     - Rejects any shell injection or command chaining metacharacters (`;&|`$><\n\r`).

4. **MCP Client Helpers (`src/repoagent/mcp_client.py`)**:
   - Unified connection parameter builders:
     - `get_repo_reader_params(workspace)`
     - `get_test_runner_params(workspace)`
     - `get_github_params()`
   - Agent framework attachments:
     - CrewAI: `get_crewai_mcp_server(params)` (for `Agent(mcps=[...])`) and `get_crewai_tools(params)` (for `Agent(tools=[...])`).
     - Google ADK: `get_adk_toolset(params)` returning `McpToolset` with `StdioConnectionParams`.
   - Direct execution helpers for raw MCP transport: `list_tools_direct` and `call_tool_direct`.

5. **Acceptance Script & Test Suite**:
   - Acceptance demo (`scripts/demo_tools.py`):
     - Clones `demo_repo` into an isolated workspace directory (`runs/demo_workspace_<run_id>`).
     - Uses `repo_reader` MCP server to inspect directory structure.
     - Uses `test_runner` MCP server to run the 12 pytest tests inside the Docker sandbox (all passed).
     - Creates branch `repoagent/<run_id>-<timestamp>` in `GITHUB_REPO` (live if token available, simulated fallback otherwise).
   - Test suite (`tests/test_mcp_tools.py`): 29 comprehensive tests verifying direct tool calls, path traversal rejection, disallowed command rejection, shell injection blocking, Docker execution, timeout enforcement, and framework adapters.

### How to run

```bash
# Build sandbox Docker container image
docker build -t repoagent-sandbox:latest sandbox/
# Or via Makefile:
make build-sandbox

# Run acceptance demo script
python scripts/demo_tools.py
# Or via Makefile:
make demo-tools

# Run all test suites (68 project tests + 12 demo_repo tests)
pytest tests/ -v
pytest demo_repo/tests/ -v

# Run lint and formatting checks
ruff check src/ tests/ scripts/ demo_repo/
ruff format --check src/ tests/ scripts/ demo_repo/
```

### Test results

| Test suite | Tests / Check | Status |
|-----------|---------------|--------|
| `tests/test_mcp_tools.py` | 29 unit tests | ✅ All passing |
| `tests/test_flow.py` | 5 unit tests | ✅ All passing |
| `tests/test_providers.py` | 7 unit tests | ✅ All passing |
| `tests/test_schemas.py` | 27 unit tests | ✅ All passing |
| `demo_repo/tests/` | 12 unit tests | ✅ All passing |
| Total Tests | 80 tests across repo | ✅ 80 passed |
| Ruff lint & formatting | 29 files | ✅ Clean (0 issues) |
| Sandbox Docker execution | `pytest` inside container | ✅ 12 passed in 0.29s (network none, non-root) |

### Known issues

- None.

---

*Next step: Step 4 (awaiting instructions)*
