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

## Step 4: CrewAI Coding and Testing Agents with Correction Loop ✅

**Date:** 2026-10-24
**Status:** Complete

### What was done

1. **Repo Writer MCP Tool Extension (`src/repoagent/mcp_servers/repo_reader.py`)**:
   - Added `write_file(path, content)` to the repository MCP server.
   - Enforces the same strict security traversal checks (`_resolve_safe`), preventing arbitrary writes outside the assigned workspace.
   - Auto-creates parent directories securely within the workspace.

2. **Hardcoded Password-Reset Plan Fixture (`src/repoagent/fixtures.py`)**:
   - Added `PASSWORD_RESET_PLAN` for `demo_repo` requirement *"Add password-reset functionality"*.
   - Specifies planned changes to `src/notes_app/auth.py`, `src/notes_app/app.py`, and `tests/test_auth.py`, with execution steps and risks. Serves as the canonical Plan until the Step 5 planner agent is integrated.

3. **CrewAI Coding Agent (`src/repoagent/agents/crewai/coding_agent.py`)**:
   - Implemented `create_coding_agent(tools, llm)` configured with repo reader/writer MCP tools.
   - Implemented `create_coding_task(agent, state, failed_tests, log_tail)` using `output_pydantic=ChangeSet`.
   - Injects previous failure context (`failed_tests` and `log_tail`) dynamically into the task description to guide self-correction on subsequent attempts.
   - Implemented `run_coding_agent(tools, state, llm, failed_tests, log_tail)` wrapper with error handling and fallback parsing.

4. **CrewAI Testing Agent (`src/repoagent/agents/crewai/testing_agent.py`)**:
   - Implemented `create_testing_agent(tools, llm)` configured with test runner MCP tools (`run_tests`, `run_lint`).
   - Implemented `create_testing_task(agent)` using `output_pydantic=TestReport`.
   - Implemented `run_testing_agent(tools, llm)` returning structured `TestReport` with execution outcomes, failed test list, and log output.

5. **Correction Loop Integration (`src/repoagent/flow.py`)**:
   - Wired the Coding Agent and Testing Agent into `RepoAgentFlow`.
   - On test failure:
     - Extracts `TestReport.failed_tests` and `log_tail` from the latest report.
     - Increments `state.retry_count`.
     - Appends each test attempt to `state.test_reports`.
     - Feeds failure diagnostics into the Coding Agent on the next loop cycle.
   - Loop limits:
     - Up to `max_retries` (default 3) iterations.
     - When retries are exhausted without passing tests, exits the loop with `RunStatus.NEEDS_HUMAN` and records explanatory error messages in `state.errors`.
   - Supports both live agent execution (`use_live_agents=True`) and custom callable injection (`coding_fn`, `testing_fn`) for deterministic, rapid unit testing.

6. **Correction Loop Test Suite & Acceptance Script**:
   - `tests/test_coding_loop.py` (5 comprehensive tests):
     - `test_coding_task_output_pydantic`: validates `ChangeSet` output schema enforcement on CrewAI tasks.
     - `test_testing_task_output_pydantic`: validates `TestReport` output schema enforcement on CrewAI tasks.
     - `test_failure_context_passed_to_coding_task`: verifies failed test list and log output are provided to the coding task prompt.
     - `test_loop_fail_fail_pass`: verifies recovery after 2 failed attempts, culminating in `status=done` and 3 recorded reports in `state.test_reports`.
     - `test_loop_fail_three_times_needs_human`: verifies termination at retry cap, ending in `status=needs_human` and 4 recorded reports in `state.test_reports`.
   - `scripts/run_coding_loop.py`:
     - Acceptance runner applying changes to a workspace clone of `demo_repo`.
     - Runs the test suite inside the Docker sandbox container (`repoagent-sandbox:latest`).
     - Demonstrates the failure recovery loop: initial failure caught -> failure context fed back to coder -> corrected code passes containerized tests -> pipeline completes.

### How to run

```bash
# Run acceptance test script (simulation + Docker sandbox runner)
python scripts/run_coding_loop.py

# Run all test suites (75 project tests + 12 demo_repo tests)
pytest tests/ -v
pytest demo_repo/tests/ -v

# Run lint and formatting checks
ruff check src/ tests/ scripts/ demo_repo/
ruff format --check src/ tests/ scripts/ demo_repo/
```

### Test results

| Test suite | Tests / Check | Status |
|-----------|---------------|--------|
| `tests/test_coding_loop.py` | 5 unit tests | ✅ All passing |
| `tests/test_mcp_tools.py` | 29 unit tests | ✅ All passing |
| `tests/test_flow.py` | 5 unit tests | ✅ All passing |
| `tests/test_providers.py` | 7 unit tests | ✅ All passing |
| `tests/test_schemas.py` | 27 unit tests | ✅ All passing |
| `demo_repo/tests/` | 12 unit tests | ✅ All passing |
| Total Tests | 87 tests across repo | ✅ 87 passed |
| Ruff lint & formatting | 34 files | ✅ Clean (0 issues) |
| Containerized Correction Loop | `scripts/run_coding_loop.py` | ✅ Verified in Docker sandbox |

### Known issues

- None.

---

*Next step: Step 5 (awaiting instructions)*

---

## Step 5: Google ADK Agents Served Over A2A, Called from CrewAI ✅

**Date:** 2026-10-25
**Status:** Complete

### What was done

1. **Repo Analyst / Planner ADK Agent (`src/repoagent/agents/adk/planner.py`)**:
   - Implemented `repo_planner` agent using Google ADK with `output_schema=PlannerOutput`.
   - Produces structured architectural `repo_map` and implementation `Plan` matching Pydantic schemas.
   - Removed `PASSWORD_RESET_PLAN` fixture from all live paths (`flow.py`, `scripts/run_coding_loop.py`), reserving it exclusively for unit test fixtures.
   - Generated A2A `AgentCard` with skills `analyze_repo` and `generate_plan`.
   - **Grounding Architecture**: The Planner receives repository context (directory tree and key source files) via client wrapper tool calls to repo-reader MCP tools (`list_tree`, `read_file`). Each tool call is measured and audited with tool name, arguments, duration, and status to `runs/<run_id>/a2a.jsonl`. The Planner does not execute autonomous tool-calling loops over A2A; instead, the client wrapper deterministically prefetches file contents and hierarchy via repo-reader and injects them directly into the prompt.
   - **Plan Path Validation & 1-Retry Cap**: Implemented deterministic path validation (`validate_plan_paths`). Verifies all modified files exist in the workspace and all new files lie within the workspace following package layout (`src/notes_app/...`, `tests/...`). Nonexistent paths (e.g. hallucinated `src/auth.py`) trigger a single retry with the real workspace tree and error feedback. If the second plan remains invalid, the flow terminates safely with `status=needs_human`.

2. **Code Reviewer ADK Agent (`src/repoagent/agents/adk/reviewer.py`)**:
   - Implemented `code_reviewer` agent using Google ADK with `output_schema=ReviewReport`.
   - Takes real disk-computed git diffs (`git add -N . && git diff HEAD`), deterministic `TestReport` (with `workspace_hash` binding), and `Plan`.
   - Replaced hardcoded 'approve' review stub with live A2A evaluation.
   - Enforces invariant: Reviewer verdict runs only after passing deterministic tests, and verdict never overrides test results.
   - If verdict is `request_changes`, actionable findings are routed back to the Coding Agent once (counting toward the retry budget).
   - Generated A2A `AgentCard` with skill `review_diff`.

3. **A2A Server Exposure & Network Configuration (`src/repoagent/agents/adk/server.py`)**:
   - Exposed each ADK agent on its own dedicated port via Starlette and `google.adk.a2a.utils.agent_to_a2a.to_a2a`:
     - Planner on `http://127.0.0.1:8001/` (`.well-known/agent-card.json`)
     - Reviewer on `http://127.0.0.1:8002/` (`.well-known/agent-card.json`)
   - Default host binds strictly to `127.0.0.1` (configurable via `REPOAGENT_A2A_HOST`).
   - Agent cards dynamically resolve URLs via `get_public_url()` using `REPOAGENT_A2A_PUBLIC_URL` or `127.0.0.1`, guaranteeing the card URL is never `0.0.0.0`.
   - CLI entry point supporting `--agent planner`, `--agent reviewer`, or `--all`.

4. **A2A Client Wrapper & Flow Integration (`src/repoagent/a2a_client.py`, `src/repoagent/flow.py`)**:
   - Implemented `A2AClient` supporting agent card fetching over HTTP (`/.well-known/agent-card.json`), task submission, JSON text extraction, and strict Pydantic model validation (`PlannerOutput.model_validate_json`, `ReviewReport.model_validate_json`).
   - Comprehensive audit logging to `runs/<run_id>/a2a.jsonl` recording `timestamp`, `agent_card_url`, `task_id`, `duration_s`, `status`, summaries, with strict sanitization preventing secrets/tokens from leaking into logs.
   - Rejection of malformed or schema-violating responses in code with errors recorded in `RunState.errors`.

5. **Replay Mode Support**:
   - Added `--replay` flag to `scripts/run_coding_loop.py`.
   - Replay runs store evidence under `runs/replay_<run_id>`, flag `is_replay: True` in `RunState` and console logs, and apply pre-recorded `KNOWN_GOOD_PASSWORD_RESET_CHANGESET`.
   - Replay mode executes the real deterministic Docker test runner and real A2A Reviewer, validating the end-to-end passing flow.

6. **Deployment Scripts & Docker Support**:
   - Added `scripts/start_agents.sh` (Linux/macOS) and `scripts/start_agents.ps1` (Windows) defaulting to `127.0.0.1`.
   - Added `Dockerfile.agents`, `docker-compose.yml`, and `docker-compose.agents.yml` for multi-container orchestration.
   - Added verification test suite `scripts/verify_step5_deliverables.py`.

7. **Testing & Invariants Preservation**:
   - Added `tests/test_a2a.py` (17 tests) covering agent card schemas, HTTP fetch, JSON cleaning, A2A error handling, schema validation rejection, audit logging with secret redaction, flow routing, nonexistent path rejection, valid path acceptance, and 1-retry then `needs_human`.
   - Strictly preserved all Step 4 invariants: deterministic test runner verification, `workspace_hash` binding, disk-derived git diffs, duplicate test rejection, atomic ChangeSet rollback, and `needs_human` retry bounds.

### How to run

```bash
# Start ADK A2A servers (defaults to 127.0.0.1)
python -m repoagent.agents.adk.server --all

# Or on Windows PowerShell:
.\scripts\start_agents.ps1

# Run Step 5 deliverables verification (real round-trips against Ollama)
python scripts/verify_step5_deliverables.py

# Run all test suites
pytest tests/ -v
pytest demo_repo/tests/ -v

# Run lint and format checks
ruff check src/ tests/ scripts/ demo_repo/
ruff format --check src/ tests/ scripts/ demo_repo/
```

### Test results

| Test file | Tests | Status |
|-----------|-------|--------|
| `tests/test_a2a.py` | 17 unit tests | ✅ All passing |
| `tests/test_changeset_applier.py` | 9 unit tests | ✅ All passing |
| `tests/test_coding_loop.py` | 5 unit tests | ✅ All passing |
| `tests/test_flow.py` | 5 unit tests | ✅ All passing |
| `tests/test_mcp_tools.py` | 31 unit tests | ✅ All passing |
| `tests/test_providers.py` | 10 unit tests | ✅ All passing |
| `tests/test_retry.py` | 5 unit tests | ✅ All passing |
| `tests/test_schemas.py` | 28 unit tests | ✅ All passing |
| `tests/test_verification_hardening.py` | 3 unit tests | ✅ All passing |
| **Project Tests Subtotal** | **113 unit tests** | **✅ 113 passed** |
| `demo_repo/tests/test_auth.py` | 6 unit tests | ✅ All passing |
| `demo_repo/tests/test_notes.py` | 6 unit tests | ✅ All passing |
| **Demo Repo Subtotal** | **12 unit tests** | **✅ 12 passed** |
| **Total Test Suite** | **125 tests** | **✅ 125 passed (100%)** |
| Ruff linting & formatting | All files | ✅ Clean (0 errors) |

---

*Next step: Step 6 (awaiting instructions)*
