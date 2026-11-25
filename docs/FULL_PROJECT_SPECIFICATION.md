# Document 1: RepoAgent — Full Project Specification

---

### 1. Project Title
**RepoAgent – Autonomous Repository-to-PR Agent**

---

### 2. Team Members
**Group 23**
- **Kushagra Pachauri** (Roll No: 230442)
- **Saurabh Jha** (Roll No: 230483)
- **Tushar Mishra** (Roll No: 230499)

---

### 3. Problem Definition

#### What problem are you addressing?
Modern software engineering requires developers to spend substantial manual effort navigating existing codebases, understanding architectural conventions, drafting implementation plans, writing code, running local builds/tests, debugging test failures, conducting quality reviews, and assembling structured pull requests. While LLMs excel at one-shot code generation, single-agent systems often hallucinate non-existent files, make breaking syntax edits, bypass local test execution, ignore linting guidelines, and fail to verify changes against repository baselines. 

RepoAgent solves this by creating an autonomous, guardrailed multi-agent system where specialized AI agents collaborate across standard protocols (Model Context Protocol and Agent-to-Agent protocol) to read the repository, plan changes, iteratively code and self-correct against a sandboxed test runner, perform architectural code reviews, and publish verified GitHub Pull Requests.

#### Who are the intended users?
- **Software Developers & Engineering Teams:** To automate routine feature requests, bug fixes, and refactoring tasks with deterministic test validation.
- **Open-Source Maintainers:** To autonomously triage feature issues, generate implementation proposals, and produce clean, ready-to-review pull requests.
- **Student Developers & Researchers:** To explore agentic collaboration patterns, heterogeneous framework interop (CrewAI + Google ADK), and protocol-driven tooling (MCP + A2A).

---

### 4. Objectives & Expected Outcomes

#### Key Objectives:
1. **Heterogeneous Agent Orchestration & State Sharing:** Build a centralized orchestrator using CrewAI Flow with Pydantic v2 type-enforced shared state (`RunState`) connecting CrewAI agents and Google ADK agents running as standalone A2A (Agent-to-Agent) microservices.
2. **Standardized Tool Integration via Model Context Protocol (MCP):** Implement standard MCP tool servers for repository inspection/editing (`repo_reader`), Docker containerized test execution (`test_runner`), and GitHub API management (`@modelcontextprotocol/server-github`).
3. **Closed-Loop Test Reflection & Self-Correction:** Establish an automated feedback loop where test/lint failures from an isolated Docker sandbox are fed directly back into the Coding Agent (capped at `max_retries=3`), gracefully transitioning to `needs_human` if unresolvable.
4. **Deterministic Preflight Verification & PR Generation:** Enforce strict git baseline comparison, file scoping (excluding framework/secret files), and post-creation verification, opening a structured pull request on GitHub containing verified test logs, coverage, and review findings.

#### Expected Outcome of the System:
A robust, end-to-end autonomous agentic application that accepts a repository path/URL and a natural-language requirement, autonomously moves through understanding, planning, implementation, Dockerized test execution, and code review, and delivers a verified GitHub Pull Request ready for human approval without touching `main` or leaking credentials.

---

### 5. Proposed Methodology

#### Agents and their Roles

| Agent | Framework | Protocol / Host | Role & Responsibilities |
| :--- | :--- | :--- | :--- |
| **Repo Analyst / Planning Agent** | Google ADK | A2A Server (`http://127.0.0.1:8001`) | Analyzes directory hierarchy, reads existing codebase conventions via MCP, validates target paths, and generates a structured implementation `Plan`. |
| **Coding Agent** | CrewAI | In-Flow / Native | Implements code edits based on the `Plan`, generating structured `ChangeSet` diffs and applying changes inside an isolated workspace. Consumes failure diagnostics during retries. |
| **Testing Agent** | CrewAI | In-Flow / Native | Invokes the Docker sandbox MCP server to execute test suites (`pytest`) and linting (`ruff`). Produces structured `TestReport` with exit codes, failed tests, and execution logs. |
| **Code Review Agent** | Google ADK | A2A Server (`http://127.0.0.1:8002`) | Performs architectural and security reviews of the real disk diff (`git diff HEAD`) against the plan and test reports. Emits a `ReviewReport` with an `approve` or `request_changes` verdict. |
| **PR Agent** | CrewAI | In-Flow / Native | Conducts baseline preflight checks against remote `main`, commits changed files to a dedicated branch (`repoagent/<run_id>`), opens the GitHub PR via GitHub MCP, and verifies PR files. |

#### Tools/APIs Used

| Category | Tool / Technology | Purpose in System |
| :--- | :--- | :--- |
| **Protocol & Tools** | **Model Context Protocol (MCP)** | Standardized protocol for tool integration using `@modelcontextprotocol/server-github`, `repo_reader` (FastMCP), and `test_runner` (FastMCP). |
| **Version Control** | **GitHub API & Git** | Remote repository operations, branch creation, atomic file commits, pull request opening, and diff verification via GitHub MCP. |
| **Sandbox Execution** | **Docker (`docker-py`)** | Isolated container sandbox (`repoagent-sandbox:latest`, Python 3.11-slim, non-root user, network disabled, resource-constrained) for secure testing. |
| **Test & Quality** | **Pytest & Ruff** | Automated test execution and lint/formatting validation within the execution sandbox. |
| **Agent Communication**| **A2A Protocol (`a2a-sdk`)** | Google Agent-to-Agent protocol over HTTP/JSON-RPC enabling CrewAI orchestrator to communicate with Google ADK agents via standardized Agent Cards. |
| **Data Validation** | **Pydantic v2** | Strict validation of all inter-agent inputs, outputs, domain models (`Plan`, `ChangeSet`, `TestReport`, `ReviewReport`, `PRSummary`), and state (`RunState`). |
| **API & UI** | **FastAPI & Streamlit** | REST backend API for managing pipeline runs and interactive Streamlit web dashboard for monitoring run status, diffs, test logs, and PR links. |
| **Observability** | **Langfuse SDK v5** | End-to-end tracing of agent execution, LLM latency, token usage, and tool invocations. |

#### LLM / Model and Agentic AI Framework

- **Agent Frameworks:**
  - **CrewAI (`1.15.28`):** Flow-based orchestration (`flow.py`), task definition, and native coding, testing, and PR agents.
  - **Google ADK (`2.12.0`):** Specialized micro-agents (Analyst/Planner and Code Reviewer) exposed as independent Starlette/A2A servers.
- **LLM Models:**
  - Supported via unified provider adapters (`src/repoagent/providers/`):
    - **Local / Offline:** Qwen2.5-Coder (`qwen2.5-coder:3b` / `qwen2.5-coder:7b`) via Ollama/vLLM using `OpenAICompatibleProvider`.
    - **Cloud / Hosted:** Google Gemini (`gemini-2.0-flash`) via `GeminiProvider`.

#### End-to-End System Workflow

```
[User Input: Requirement + Target Repo]
                 │
                 ▼
     ┌───────────────────────┐
     │  FastAPI / Streamlit  │
     └───────────┬───────────┘
                 │
                 ▼
     ┌────────────────────────────────────────────────────────┐
     │             CrewAI Flow Orchestrator                   │
     │            Shared State: RunState (Pydantic v2)        │
     └───────┬────────────────────────────────────────┬───────┘
             │                                        │
             ▼ (A2A Protocol: Port 8001)               ▼ (A2A Protocol: Port 8002)
   ┌───────────────────────┐                ┌──────────────────────┐
   │ Repo Analyst / Planner│                │  Code Review Agent   │
   │      (Google ADK)     │                │     (Google ADK)     │
   └───────────┬───────────┘                └──────────▲───────────┘
               │ Plan                                  │ Passing Tests + Diff
               ▼                                       │
   ┌───────────────────────┐                ┌──────────┴───────────┐
   │     Coding Agent      │                │    Testing Agent     │
   │       (CrewAI)        │                │       (CrewAI)       │
   └───────────┬───────────┘                └──────────▲───────────┘
               │ ChangeSet                             │
               ▼                                       │
   ┌───────────────────────┐      Test Execution       │
   │  Isolated Workspace   ├───────────────────────────┘
   │  (Repo Reader/Writer) │  (Test Runner MCP in Docker)
   └───────────────────────┘
               │
               │ [If Tests Fail & retries < 3]
               └───► Diagnostics / logs fed back to Coding Agent
               │ [If retries >= 3]
               └───► Terminate with status: needs_human
               │
               │ [On Test Pass + Review Approval]
               ▼
   ┌────────────────────────────────────────────────────────┐
   │                       PR Agent                         │
   │    Preflight Check → Create Branch → Commit Files      │
   │             Open GitHub PR → Verify Remote PR          │
   └───────────────────────────┬────────────────────────────┘
                               │
                               ▼
            [Verified GitHub Pull Request #PR]
```

1. **Intake & Initialization:** User submits requirement; orchestrator initializes an isolated workspace cloned from the target repository and populates `RunState`.
2. **Analysis & Planning (A2A):** Flow prefetches directory tree and key files via `repo_reader` MCP, calling the ADK Planner over A2A. The planner generates an implementation `Plan`.
3. **Coding Execution:** Coding Agent receives the `Plan`, generates a structured `ChangeSet`, and writes modifications via `repo_writer` MCP.
4. **Sandboxed Testing & Reflection Loop:** Testing Agent executes `pytest` and `ruff` inside the Docker sandbox container (`test_runner` MCP).
   - If tests fail and `retry_count < 3`: Failure trace is fed back to the Coding Agent to iteratively fix the code.
   - If retries exhaust (`retry_count >= 3`): Pipeline terminates safely with status `needs_human`.
5. **Architectural Review (A2A):** Upon test pass, the ADK Reviewer inspects the actual `git diff HEAD` and test metrics over A2A, returning a verdict (`approve` or `request_changes`).
6. **PR Creation & Verification:** If approved and `--create-pr` is enabled, PR Agent validates baseline integrity, pushes changed files to `repoagent/<run_id>`, opens the GitHub PR via GitHub MCP, verifies changed files match the disk diff, and outputs structured `report.json` and `report.md`.

---

### 6. Feasibility & Resources

#### Data Sources & Benchmark Fixtures
- **Target Repository:** `Kushagra-Pachauri/demo_repo` (Notes API with authentication, user models, SQLite storage, and 12 baseline unit tests).
- **Benchmark Tasks & Replay Reference:** Registered reference feature requirements (e.g., `"Add password-reset functionality"`) with deterministic unit test baselines to benchmark agent performance offline without live API costs.

#### APIs & Tooling
- **GitHub MCP Server:** `@modelcontextprotocol/server-github` running under Node.js / `npx` with fine-grained personal access token (`GITHUB_TOKEN`).
- **Ollama:** Local inference engine hosting `qwen2.5-coder:3b` / `qwen2.5-coder:7b` at `http://127.0.0.1:11434`.
- **Google Gemini API:** Optional cloud inference endpoint (`gemini-2.0-flash`).
- **Langfuse:** Tracing platform for LLM execution logs and latency metrics.

#### Software & Frameworks
- **Runtime:** Python 3.11.15 in dedicated virtual environment (`.venv`).
- **Agent Orchestration:** `crewai==1.15.28`, `google-adk==2.12.0`, `a2a-sdk==1.2.2`.
- **Tool Protocol:** `mcp[cli]==1.28.1`, `fastmcp`.
- **Containerization:** Docker Desktop / Docker Engine, `docker==7.2.0`.
- **Validation & Web:** `pydantic==2.12.5`, `fastapi==0.141.1`, `uvicorn==0.54.0`, `streamlit==1.45.1`.
- **Testing & Quality:** `pytest==8.4.1`, `pytest-asyncio==1.0.0`, `ruff==0.11.13`.

#### Compute & Resources Required
- Standard local development workstation (Windows 11 / Linux / macOS) with 16 GB+ RAM.
- Local CPU / GPU compute for Ollama 3B/7B model execution.
- Docker engine with 2 CPUs and 2 GB allocated memory for the test sandbox.
- No paid cloud infrastructure or paid third-party APIs required for core execution.

---

### 7. Team Members & Individual Understanding

| Member | Role & System Responsibility | Individual Understanding & Technical Ownership |
| :--- | :--- | :--- |
| **Kushagra Pachauri** (230442) | **Lead Agent Architecture & Core Orchestration** | Designs the central `RepoAgentFlow` orchestrator, shared Pydantic `RunState` model hierarchy, and the A2A protocol integration. Owns inter-agent communication between CrewAI and Google ADK microservices, provider adapters (Gemini and Ollama/vLLM), and strict safety invariant gating (retry limits, path validation, state consistency). |
| **Saurabh Jha** (230483) | **Tool Protocols, Execution Sandbox & Web UI** | Builds and maintains the Model Context Protocol (MCP) tool servers (`repo_reader`, `test_runner`), Docker execution container environment (`repoagent-sandbox:latest`), and security sandboxing. Develops the FastAPI REST backend endpoints and the Streamlit web dashboard for interactive pipeline execution and monitoring. |
| **Tushar Mishra** (230499) | **Quality Assurance, PR Automation & Evaluation** | Implements the PR Agent and GitHub MCP integration, ensuring baseline preflight verification, atomic file commits, and post-PR creation verification. Develops comprehensive automated unit/integration test suites, regression test harnesses, benchmark replay validation, and Langfuse observability instrumentation. |

---

### 8. Project Timeline

| Phase / Activity | Week(s) | Key Deliverables |
| :--- | :--- | :--- |
| **1. Project Setup & Design Freeze** | Week 1 | Repository scaffold, Pydantic v2 schemas (`RunState`), target `demo_repo`, and architecture specification (`docs/ARCHITECTURE.md`). |
| **2. Orchestration & Providers** | Week 2 | `RepoAgentFlow` pipeline skeleton, retry logic, provider adapters for Ollama & Gemini, and CLI entry point (`repoagent.run`). |
| **3. MCP Tool Servers & Sandbox** | Week 3 | FastMCP `repo_reader` and `test_runner` servers, non-root Docker execution sandbox (`sandbox/Dockerfile`), and GitHub MCP configuration. |
| **4. Coding Loop & Self-Correction** | Week 4 | CrewAI Coding and Testing agents, iterative error diagnostic reflection loop (capped at 3 retries), and ChangeSet application. |
| **5. Google ADK & A2A Integration** | Week 5 | ADK Planner (port 8001) and ADK Reviewer (port 8002) served over A2A protocol; A2A client integration in Flow; reference replay runner. |
| **6. GitHub PR Automation & Verification** | Week 6 | PR Agent with preflight baseline verification, atomic file commits, GitHub PR creation, markdown report generation, and idempotent promotion. |
| **7. Guardrails & Callbacks** | Week 7 | Security guardrails (secret detection, prompt injection defense, path traversal blocking), task monitoring callbacks, and structured logging. |
| **8. Observability & REST API / UI** | Week 8 | Langfuse tracing integration, FastAPI REST endpoints, and interactive Streamlit monitoring dashboard. |
| **9. Evaluation, Packaging & Final Demo** | Week 9 | Benchmark evaluation suite (success rate, token efficiency, latency), containerized deployment (`docker-compose.yml`), project documentation, and live demo. |

---

### 9. Team Responsibility Matrix

| Project Activity | Kushagra Pachauri (230442) | Saurabh Jha (230483) | Tushar Mishra (230499) |
| :--- | :---: | :---: | :---: |
| **Multi-Agent Flow Orchestration** | **Lead** | Support | Support |
| **Pydantic State & Domain Schemas** | **Lead** | Support | Support |
| **Google ADK & A2A Integration** | **Lead** | Support | Support |
| **LLM Provider Adapters (Ollama/Gemini)**| **Lead** | Support | Support |
| **MCP Tool Servers (Repo Reader/Writer)** | Support | **Lead** | Support |
| **Docker Sandbox & Container Security** | Support | **Lead** | Support |
| **FastAPI REST API & Streamlit UI** | Support | **Lead** | Support |
| **GitHub MCP & PR Agent Automation** | Support | Support | **Lead** |
| **Test Suites, Quality & Linting** | Support | Support | **Lead** |
| **Observability, Tracing & Metrics** | Support | Support | **Lead** |
| **Evaluation Harness & Benchmarking** | Support | Support | **Lead** |
