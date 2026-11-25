# RepoAgent — Team Work Distribution & Viva Guide

> **Group 23**: Kushagra Pachauri (230442), Saurabh Jha (230483), Tushar Mishra (230499)  
> **Ratio**: Kushagra (~38% - Lead Architecture), Saurabh (~31% - Tooling & UI), Tushar (~31% - QA, PR & Evaluation)

Yeh document project ke saare kaam (Completed Steps 1–6 + Future Steps 7–10) ko 3 members ke beech perfectly distribute karta hai. Har member ka technical role, code files, viva presentation topics, aur contribution percentage clearly defined hai.

---

## 1. High-Level Summary & Contribution Share

| Team Member | Roll No. | Primary Role | Weightage | Core Domain Ownership |
| :--- | :---: | :--- | :---: | :--- |
| **Kushagra Pachauri** | **230442** | **Project Lead & Agent Orchestrator Architect** | **~38%** | CrewAI Flow, Pydantic v2 `RunState`, Google ADK & A2A Protocol, LLM Provider Adapters, State Consistency & Promotion Safety. |
| **Saurabh Jha** | **230483** | **Tools, Sandbox Security & Frontend/API Engineer** | **~31%** | FastMCP Tool Servers (`repo_reader`, `test_runner`), Docker Sandbox Isolation, ChangeSet Applier, FastAPI & Streamlit UI. |
| **Tushar Mishra** | **230499** | **QA, GitHub PR Automation & Evaluation Engineer** | **~31%** | GitHub MCP Integration, PR Agent, Baseline Preflight Checks, Test Suite Verification (149 tests), Langfuse Tracing & Metrics. |

---

## 2. Detailed Member-Wise Distribution

---

### 👤 Member 1: Kushagra Pachauri (230442) — ~38%
**Role: Lead Agent Architecture, State Machine & Cross-Framework Integration**

#### 🔹 Done Work (Step 1 to Step 6):
1. **CrewAI Flow Orchestrator (`src/repoagent/flow.py`):**
   - Central state machine design (`analyze` → `plan` → `code` → `test` → `review` → `open_pr`).
   - Self-correction retry routing logic: Capped at `max_retries=3`, transitioning safely to `RunStatus.NEEDS_HUMAN` when budget exhausts.
2. **Pydantic v2 State Architecture (`src/repoagent/schemas.py`):**
   - 12 core domain models: `RunState`, `Plan`, `ChangeSet`, `TestReport`, `ReviewReport`, `PRSummary`, `RunStatus`, `ReviewVerdict`.
   - Invariant enforcement with `validate_assignment=True`.
3. **Google ADK & A2A Protocol Integration (`src/repoagent/a2a_client.py`, `src/repoagent/agents/adk/server.py`):**
   - Multi-agent microservices setup: Planner on `http://127.0.0.1:8001/` and Reviewer on `http://127.0.0.1:8002/`.
   - Flow A2A client integration: Agent card resolution (`/.well-known/agent-card.json`), HTTP JSON-RPC task calls, and token redaction in `a2a.jsonl`.
4. **LLM Provider Adapters (`src/repoagent/providers/`):**
   - Abstract `LLMProvider` factory supporting local Ollama (`qwen2.5-coder:3b`/`7b`) via `OpenAICompatibleProvider` and Google Gemini (`gemini-2.0-flash`) via `GeminiProvider`.
5. **State Consistency & Safe Promotion Policy (`src/repoagent/flow.py`, `src/repoagent/run.py`):**
   - Unified `evaluate_done_gate` logic shared across Flow and PR promotion.
   - Deterministic verification between `run_state.json` and `report.json`.
   - Replay benchmark locking (`REGISTERED_REPLAY_BENCHMARKS`).

#### 🔹 Future Scope (Step 7 to Step 10):
- **Step 7 (Lead):** Core security guardrails pipeline and task lifecycle callback hooks in `flow.py`.
- **Step 10 (Lead):** Multi-agent orchestration benchmarking and final system architecture report.

#### 🎤 What to Speak in Viva / Evaluation:
> *"Maine RepoAgent ka brain aur central orchestration design kiya hai. Maine CrewAI Flow ko use karke pura state machine implement kiya jo step-by-step pipeline ko drive karta hai. Maine Pydantic v2 se typed shared state (`RunState`) banaya taaki agents ke beech data integrity bani rahe. Sath hi maine Google ADK microservices ko CrewAI se connect karne ke liye A2A protocol client implement kiya aur Gemini aur Ollama dono ke liye dynamic provider adapters develop kiye."*

---

### 👤 Member 2: Saurabh Jha (230483) — ~31%
**Role: MCP Tool Protocols, Docker Sandbox Security & UI/API**

#### 🔹 Done Work (Step 1 to Step 6):
1. **Model Context Protocol (MCP) Tool Servers (`src/repoagent/mcp_servers/`):**
   - **`repo_reader.py` (FastMCP):** Tools for `list_tree`, `read_file`, `write_file`, and `search_code`.
   - **Path Traversal Security:** Implemented `_resolve_safe` to strictly prevent directory traversal attacks (`../../`), absolute root paths, or Windows UNC shares.
2. **Docker Sandbox Execution Environment (`sandbox/Dockerfile`, `src/repoagent/mcp_servers/test_runner.py`):**
   - Non-root container environment (`repoagent-sandbox:latest`, Python 3.11-slim, non-root user `sandbox`).
   - Execution security: `network_mode="none"`, 1 CPU limit, 512MB RAM cap, and 120s timeout killing.
   - Command Whitelisting: Enforced allowlist strictly permitting `pytest` and `ruff`, rejecting shell injection chaining (`;`, `&&`, `|`).
3. **Workspace ChangeSet Applier & Rollback:**
   - Disk patching logic applying LLM diffs to the isolated workspace with syntax validation and rollback on syntax errors.
4. **Demo Repo & Environment Scripts:**
   - Target benchmark repository setup (`demo_repo/` with Notes API and 12 unit tests).
   - Multi-agent startup automation scripts (`scripts/start_agents.ps1`, `scripts/start_agents.sh`).

#### 🔹 Future Scope (Step 7 to Step 10):
- **Step 7 (Support):** Input sanitization and path guardrails enforcement.
- **Step 9 (Lead):** FastAPI backend REST endpoints (`/runs`, `/runs/{id}`) and Streamlit interactive web dashboard for real-time visual run monitoring.
- **Step 10 (Support):** `docker-compose.yml` multi-container packaging.

#### 🎤 What to Speak in Viva / Evaluation:
> *"Mera main focus execution security aur tool integration par raha hai. Maine Anthropic ka Model Context Protocol (MCP) use karke `repo_reader` aur `test_runner` servers banaye. Maine Docker sandbox environment develop kiya jisme generated code bina internet access ke non-root user ke andar isolated run hota hai, jisse system attack ya crash se bachta hai. Sath hi maine directory traversal rokne ke liye strict path resolution guards implement kiye hain."*

---

### 👤 Member 3: Tushar Mishra (230499) — ~31%
**Role: GitHub PR Automation, Quality Assurance & Evaluation**

#### 🔹 Done Work (Step 1 to Step 6):
1. **GitHub MCP Integration & PR Agent (`src/repoagent/pr_agent.py`):**
   - Integration with `@modelcontextprotocol/server-github` via stdio transport.
   - Tools mapped: `create_branch`, `push_files`, `create_pull_request`, `get_pull_request`, `get_pull_request_files`.
2. **Git Baseline Preflight Checks & Scope Guards:**
   - Verified local workspace matches remote `main` baseline before creating branches.
   - Strict Push Scoping: Filtered push candidates to only changed workspace files, strictly excluding `.env`, `runs/`, RepoAgent framework files, and credentials.
3. **Live PR Creation & Verification:**
   - Successfully created and verified real **PR #1** on GitHub (`Kushagra-Pachauri/demo_repo/pull/1`).
   - Post-creation verification confirming remote changed files strictly match local disk diffs.
   - PR Idempotency: Preventing duplicate PR creation on retried runs.
4. **Comprehensive Test Suite & QA (149 Tests):**
   - Authored unit/integration tests in `tests/test_pr_agent.py`, `tests/test_mcp_tools.py`, and `tests/test_verification_hardening.py`.
   - Verified 100% pass rate (149/149 tests), clean Ruff linting, and zero test folder pollution.
5. **Run Reporting Engine:**
   - Formatted report generators: `runs/<run_id>/report.json` and `runs/<run_id>/report.md`.

#### 🔹 Future Scope (Step 7 to Step 10):
- **Step 7 (Support):** Automated secret detection scanner preventing API keys from leaking into generated PRs.
- **Step 8 (Lead):** Langfuse SDK v5 observability for end-to-end tracing of agent latency, token consumption, and cost tracking.
- **Step 10 (Lead):** Automated benchmark evaluation suite (measuring pass rate, retry counts, time-to-PR).

#### 🎤 What to Speak in Viva / Evaluation:
> *"Maine Git version control, GitHub PR automation, aur testing/quality assurance par kaam kiya hai. Maine GitHub MCP server ke through automated PR Agent banaya jo branch creation, atomic commit pushing, aur PR opening perform karta hai. Maine baseline preflight checks implement kiye taaki purane code par galat PR na bane aur ensure kiya ki koi secret ya framework file GitHub par leak na ho. Sath hi maine project ka 149-test suite maintain kiya hai jisse 100% reliability verify hoti hai."*

---

## 3. Team Responsibility Matrix (Official Table for Report)

| Project Module / Milestone | Kushagra Pachauri (Lead) | Saurabh Jha | Tushar Mishra |
| :--- | :---: | :---: | :---: |
| **System Architecture & Flow Orchestrator** | **Lead (100%)** | Support | Support |
| **Pydantic State Modeling (`RunState`)** | **Lead (80%)** | Support (10%) | Support (10%) |
| **Google ADK & A2A Protocol Microservices**| **Lead (85%)** | Support (15%) | — |
| **LLM Provider Adapters (Ollama/Gemini)** | **Lead (80%)** | Support (20%) | — |
| **MCP Tool Servers (`repo_reader`, etc.)** | Support (15%) | **Lead (85%)** | — |
| **Docker Sandbox Container & Security** | — | **Lead (90%)** | Support (10%) |
| **Workspace ChangeSet Applier & Rollback** | Support (20%) | **Lead (80%)** | — |
| **GitHub MCP & PR Agent Automation** | Support (10%) | — | **Lead (90%)** |
| **Baseline Preflight & Scope Protection** | Support (15%) | — | **Lead (85%)** |
| **Unit & Integration Test Suites (QA)** | Support (20%) | Support (20%) | **Lead (60%)** |
| **FastAPI REST API & Streamlit Dashboard** | Support (20%) | **Lead (70%)** | Support (10%) |
| **Observability & Tracing (Langfuse)** | Support (15%) | Support (15%) | **Lead (70%)** |
| **Evaluation Harness & Benchmark Metrics** | Support (20%) | Support (10%) | **Lead (70%)** |
| **Overall Project Contribution Share** | **~38%** | **~31%** | **~31%** |

---

## 4. Evaluator Cross-Questioning Quick Reference

| Potential Evaluator Question | Best Person to Answer | Quick Answer Strategy |
| :--- | :---: | :--- |
| **"Flow me states kaise manage ho rahi hain aur retry kab rukta hai?"** | **Kushagra** | Explain `RepoAgentFlow`, `RunState`, `max_retries=3`, and `needs_human` exit. |
| **"CrewAI aur Google ADK aapas me kaise baat karte hain?"** | **Kushagra** | Explain A2A protocol, Agent Cards, and ports 8001/8002 over HTTP. |
| **"Docker sandbox me code run karne ka kya procedure hai?"** | **Saurabh** | Explain `test_runner` FastMCP, non-root user, `network_mode=none`, and command whitelist. |
| **"Agar LLM malicious file delete karne ka code likh de toh?"** | **Saurabh** | Explain `_resolve_safe` in `repo_reader` and workspace path boundary validation. |
| **"GitHub PR kaise banti hai aur security kaise check hoti hai?"** | **Tushar** | Explain GitHub MCP tools, baseline preflight check, and push scoping filter. |
| **"Project ki testing status aur pass rate kya hai?"** | **Tushar** | Explain 149 unit/integration tests passing (100%), 0 ruff errors, and zero test pollution. |
