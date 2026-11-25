# Document 2: RepoAgent — Status Report of Work Done Till Now

---

### 1. Project Title
**RepoAgent – Autonomous Repository-to-PR Agent (Mid-Project Progress Report)**

---

### 2. Team Members
**Group 23**
- **Kushagra Pachauri** (Roll No: 230442)
- **Saurabh Jha** (Roll No: 230483)
- **Tushar Mishra** (Roll No: 230499)

---

### 3. Problem Addressed & Current Scope

#### Problem Addressed
Single-agent AI systems struggle with software engineering workflows because they lack architectural understanding of multi-file repositories, hallucinate nonexistent module paths, cannot execute or debug against tests locally, and lack the guardrails needed to avoid corrupting git branches or leaking secrets. 

#### Current Scope Achieved
As of **Step 6**, the core agentic pipeline is fully implemented, verified, and frozen. The system can take a repository task, coordinate CrewAI and Google ADK agents across standard MCP and A2A protocols, execute tests within a locked-down Docker container, self-correct upon test failures, conduct architectural reviews, and successfully open and verify real pull requests on GitHub.

---

### 4. Objectives Achieved vs. Status

| Objective | Target Requirement | Status | Current Achievement / Implementation Details |
| :--- | :--- | :---: | :--- |
| **1. Typed State Sharing** | Pydantic v2 `RunState` shared across all stages | ✅ Complete | 12 domain models and enums in `schemas.py`; validated mutations with `validate_assignment=True`. |
| **2. MCP Tool Integration** | Standardized tool integration via MCP | ✅ Complete | Three MCP servers: `@modelcontextprotocol/server-github`, `repo_reader` (FastMCP), and `test_runner` (FastMCP). |
| **3. Isolated Sandboxing** | Safe code execution without network or root access | ✅ Complete | Dedicated Docker sandbox (`repoagent-sandbox:latest`, Python 3.11-slim, non-root `sandbox`, `network_mode="none"`). |
| **4. Closed-Loop Correction** | Feedback loop for failed tests | ✅ Complete | CrewAI Coding Agent receives structured failure traces and log tail; automatically recovers up to `max_retries=3`. |
| **5. Dual Framework Interop** | Both CrewAI and Google ADK working together | ✅ Complete | CrewAI Flow acts as orchestrator; Google ADK agents (Planner & Reviewer) run as A2A microservices over HTTP. |
| **6. Real GitHub PR Creation** | Automated branch, commit, and PR generation | ✅ Complete | Real PR #1 created and verified on GitHub (`Kushagra-Pachauri/demo_repo`) using only GitHub MCP tools. |
| **7. Replay & Safety Gating** | Safeguards preventing false completion or replay misuse | ✅ Complete | Replay mode restricted to registered benchmarks; `--from-run` promotion workflow; PR creation idempotent. |

---

### 5. Detailed Technical Implementation Completed

#### 1. Step 1: Project Setup & Design Freeze
- **Scaffold & Build System:** Pinned cross-framework dependencies between `crewai==1.15.28` and `google-adk==2.12.0` in `pyproject.toml` using `hatchling.build` under Python 3.11.15.
- **Pydantic v2 Schemas (`src/repoagent/schemas.py`):** Implemented 12 typed data models: `RunState`, `Plan`, `ChangeSet`, `TestReport`, `ReviewReport`, `PRSummary`, `PlannedFile`, `FileChange`, `ReviewFinding`, `RunStatus`, and `ReviewVerdict`.
- **Target Demo Repository (`demo_repo/`):** Full Notes API built with user authentication, SQLite database, and 12 unit tests serving as the baseline benchmark.

#### 2. Step 2: Flow Orchestration & Provider Abstraction
- **LLM Provider Layer (`src/repoagent/providers/`):** Implemented abstract `LLMProvider` interface with concrete implementations:
  - `GeminiProvider`: Connects to `gemini-2.0-flash`.
  - `OpenAICompatibleProvider`: Connects to local models via Ollama (`qwen2.5-coder`) or vLLM.
- **CrewAI Flow Orchestrator (`src/repoagent/flow.py`):** Implemented `RepoAgentFlow` driving the pipeline: `analyze` → `plan` → `code` → `test` → [retry / review] → `open_pr`.
- **CLI Runner (`src/repoagent/run.py`):** Configured CLI flags (`--repo`, `--requirement`, `--provider`, `--max-retries`, `--from-run`, `--create-pr`).

#### 3. Step 3: Model Context Protocol (MCP) Tool Servers
- **Repo Reader MCP (`src/repoagent/mcp_servers/repo_reader.py`):** Exposes `list_tree`, `read_file`, `write_file`, and `search_code`. Enforces `_resolve_safe` to block directory traversal (`../`), absolute paths, and UNC paths.
- **Test Runner MCP (`src/repoagent/mcp_servers/test_runner.py`):** Runs tests inside Docker sandbox with resource limits (1 CPU, 512MB RAM, 120s timeout) and strict command allowlisting (`pytest`, `ruff`).
- **GitHub MCP Integration (`src/repoagent/mcp_client.py`):** Connects to `@modelcontextprotocol/server-github` via stdio transport with token authentication.

#### 4. Step 4: CrewAI Coding & Testing Loop with Self-Correction
- **Coding Agent (`src/repoagent/agents/crewai/coding_agent.py`):** Consumes `Plan`, emits structured `ChangeSet`, and ingests previous error logs during retries.
- **Testing Agent (`src/repoagent/agents/crewai/testing_agent.py`):** Invokes test runner MCP and formats results as `TestReport`.
- **Correction Loop:** Automatically routes failed test feedback back to the Coding Agent up to 3 retries; cleanly terminates with `needs_human` if budget is exhausted.

#### 5. Step 5: Google ADK Agents Served Over A2A Protocol
- **ADK Planner Agent (`src/repoagent/agents/adk/planner.py`):** Hosted at `http://127.0.0.1:8001/`. Analyzes repository layout and produces `Plan` with deterministic path validation and a 1-retry budget on invalid paths.
- **ADK Reviewer Agent (`src/repoagent/agents/adk/reviewer.py`):** Hosted at `http://127.0.0.1:8002/`. Inspects real git diffs (`git diff HEAD`) and test results to issue `ReviewReport` (`approve` or `request_changes`).
- **A2A Client (`src/repoagent/a2a_client.py`):** Fetches agent cards via HTTP, submits tasks, logs audit records to `runs/<run_id>/a2a.jsonl`, and redacts all secrets.

#### 6. Step 6: PR Agent, Real GitHub PR & Report Generation
- **PR Agent (`src/repoagent/pr_agent.py`):**
  - **Baseline Preflight:** Reads remote files from `main` to verify the workspace baseline matches before creating branches.
  - **Push Scoping:** Strictly scopes commits to modified feature files, excluding RepoAgent framework code and secrets.
  - **GitHub PR Creation:** Opens PR on GitHub via GitHub MCP tools (`create_branch`, `push_files`, `create_pull_request`).
  - **Verification:** Reads back PR files via `get_pull_request_files` and verifies remote diff matches local disk changes.
  - **Reports:** Writes `runs/<run_id>/report.json` and `runs/<run_id>/report.md`.
- **Verified GitHub PR:** Successfully created **PR #1** on repository `Kushagra-Pachauri/demo_repo`:
  - URL: [https://github.com/Kushagra-Pachauri/demo_repo/pull/1](https://github.com/Kushagra-Pachauri/demo_repo/pull/1)
  - Branch: `repoagent/replay_f70d568d`
  - Changed Files: Exactly 3 intended files (`app.py`, `auth.py`, `test_auth.py`). 0 leaked files.

#### 7. Post-Step 6 Safety Enhancements: Replay Protection & Promotion Workflow
- **Replay Protection:** Replay mode (`--replay`) is strictly bound to `REGISTERED_REPLAY_BENCHMARKS`. Custom natural language feature requests in replay mode are blocked immediately before modifying any workspace.
- **Promotion Workflow (`--from-run <RUN_ID>`):** Safely validates state consistency between `run_state.json` and `report.json`, verifies workspace containment, runs the full DONE gate, and promotes an existing verified run to a PR.
- **PR Idempotency:** Prevents duplicate PR creation when a run is re-evaluated or retried.

---

### 6. Verification, Test Results & Quality Metrics

All test suites pass with zero warnings, zero failures, and 100% clean linting:

| Test Suite / Inspection | File / Component | Test Count | Status |
| :--- | :--- | :---: | :---: |
| **A2A Agent Integration** | `tests/test_a2a.py` | 17 | ✅ Passed |
| **ChangeSet Applier & Rollback** | `tests/test_changeset_applier.py` | 9 | ✅ Passed |
| **Coding & Testing Loop** | `tests/test_coding_loop.py` | 5 | ✅ Passed |
| **Flow Orchestrator & State** | `tests/test_flow.py` | 6 | ✅ Passed |
| **MCP Tools (Repo & Docker)** | `tests/test_mcp_tools.py` | 31 | ✅ Passed |
| **PR Agent & Promotion Workflow** | `tests/test_pr_agent.py` | 15 | ✅ Passed |
| **LLM Provider Adapters** | `tests/test_providers.py` | 10 | ✅ Passed |
| **Retry & Gating Logic** | `tests/test_retry.py` | 5 | ✅ Passed |
| **Pydantic Schemas** | `tests/test_schemas.py` | 28 | ✅ Passed |
| **Verification & Security Hardening**| `tests/test_verification_hardening.py` | 11 | ✅ Passed |
| **Demo Repository Tests** | `demo_repo/tests/` | 12 | ✅ Passed |
| **Total Test Suite** | **Across entire repository** | **149 tests** | **✅ 100% Passed** |
| **Code Formatting** | `ruff format --check` | 60 files | ✅ Clean (0 issues) |
| **Static Code Analysis** | `ruff check` | 60 files | ✅ Clean (0 issues) |
| **Dependency Integrity** | `pip check` | Full `.venv` | ✅ Clean (No broken requirements) |

---

### 7. Team Individual Contributions Till Now

| Member | Completed Components & Technical Deliverables Till Now |
| :--- | :--- |
| **Kushagra Pachauri** (230442) | - Designed and implemented `RepoAgentFlow` orchestrator managing state transitions and retry routing.<br>- Built Pydantic v2 domain schemas (`RunState`, `Plan`, `ChangeSet`, `TestReport`, `ReviewReport`, `PRSummary`).<br>- Engineered A2A client integration and server wrappers for ADK Planner (port 8001) and ADK Reviewer (port 8002).<br>- Implemented provider adapters for Google Gemini and local Ollama Qwen2.5-Coder.<br>- Designed the unified `evaluate_done_gate` and state consistency verification policy. |
| **Saurabh Jha** (230483) | - Built the `repo_reader` FastMCP tool server with path traversal security (`_resolve_safe`).<br>- Configured and built the Docker sandbox container (`repoagent-sandbox:latest`) for non-root, network-isolated testing.<br>- Developed the `test_runner` FastMCP tool server with command allowlisting and resource constraints.<br>- Implemented the atomic workspace ChangeSet applier with syntax verification and rollback handling.<br>- Created shell and PowerShell agent automation scripts (`start_agents.ps1`, `start_agents.sh`). |
| **Tushar Mishra** (230499) | - Implemented the `PR Agent` integrating `@modelcontextprotocol/server-github` via stdio transport.<br>- Engineered remote baseline preflight verification and strict file scoping preventing framework leaks.<br>- Built the automated post-creation PR verification confirming remote diff against local changes.<br>- Verified live GitHub PR #1 on `Kushagra-Pachauri/demo_repo`.<br>- Authored comprehensive unit and regression tests in `test_pr_agent.py`, `test_mcp_tools.py`, and `test_verification_hardening.py`. |

---

### 8. Work Remaining for Completion (Future Steps)

| Step / Milestone | Planned Deliverable | Planned Technical Content |
| :---: | :--- | :--- |
| **Step 7** | **Security Guardrails & Callbacks** | Secret detection scanner, prompt injection defense, and task lifecycle callback hooks. |
| **Step 8** | **Langfuse Observability** | OpenTelemetry/Langfuse SDK v5 tracing for agent latency, token usage, and cost tracking. |
| **Step 9** | **FastAPI & Streamlit UI** | REST API endpoints for trigger/status and interactive web dashboard for real-time run monitoring. |
| **Step 10** | **Evaluation Harness & Cloud Package** | Automated benchmark evaluation suite, multi-container Docker Compose packaging, and final report. |
