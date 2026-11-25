# RepoAgent — Observability, API, Dashboard & Deployment Guide (Step 8)

**Date:** November 20, 2026  
**Version:** 0.1.0  
**Status:** Completed & Verified  

---

## 1. Observability Architecture

RepoAgent implements an end-to-end, multi-layered observability architecture spanning:
1. **Local Append-Only Audit Logging (`EventLogger`):** Records structured JSONL events to `runs/<run_id>/events.jsonl` with automated credential redaction, summary truncation, and fail-safe serialization.
2. **Distributed Tracing (`LangfuseTracker`):** Ingests events from `EventLogger` and lifecycle callbacks into Langfuse, creating root traces per run, agent-level spans, tool-level spans, and stage transitions.
3. **HTTP REST API (`FastAPI`):** Exposes endpoints to initiate runs, query execution status, inspect findings, and retrieve paginated, sanitized audit logs.
4. **Interactive Monitoring Dashboard (`Streamlit`):** Connects to the FastAPI backend to visualize run metrics, pipeline progression, agent activity, test execution results, code diffs, and Langfuse trace links.

```
+-----------------------------------------------------------------------------------+
|                            RepoAgent Execution Lifecycle                          |
|  Understand -> Plan (ADK) -> Code (CrewAI) -> Test (Sandbox) -> Review (ADK) -> PR|
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                        Central EventLogger (`events.jsonl`)                       |
|   - Credential Redaction      - Truncation Safety       - Atomic Disk Persistence  |
+-----------------------------------------------------------------------------------+
                                         |
                  +----------------------+----------------------+
                  v                                             v
+----------------------------------+          +----------------------------------+
|   Langfuse Distributed Tracing   |          |      FastAPI Backend Engine      |
|  - Root trace per run_id         |          |  - POST /runs (Async/Sync)       |
|  - Agent spans (Planner/Reviewer)|          |  - GET  /runs/{id}               |
|  - Tool spans (MCP/Sandbox)      |          |  - GET  /runs/{id}/events        |
|  - Exact token/cost tracking     |          |  - GET  /health                  |
+----------------------------------+          +----------------------------------+
                                                                ^
                                                                |
                                              +----------------------------------+
                                              |   Streamlit Monitoring Web UI    |
                                              |  - Run Overview Metrics          |
                                              |  - Pipeline Progress Tracker     |
                                              |  - Code Diffs & Test Logs        |
                                              |  - Direct Langfuse Deep-Links    |
                                              +----------------------------------+
```

---

## 2. Langfuse Integration

Langfuse provides open-source, OpenTelemetry-compatible distributed tracing for RepoAgent runs.

### Key Guarantees
- **Unified Correlation:** Root trace ID matches `trace_<run_id>`, uniquely correlating all CrewAI and Google ADK activities to the exact RepoAgent run.
- **Fail-Safe Operation:** Tracing calls are wrapped in non-blocking, fail-safe handlers. If Langfuse is unreachable or unconfigured, RepoAgent logs a diagnostic message and executes normally without disrupting security gates or code generation.
- **Credential Hygiene:** Prior to dispatching inputs, outputs, or error traces to Langfuse, all content is processed through `redact_text_for_tracing()`.
- **Honest Metrics:** Token counts and monetary costs are recorded **strictly** when reported by the underlying LLM provider. When running local providers (such as Ollama `qwen2.5-coder`), tokens and costs are marked as unavailable (`None`), never fabricated.

---

## 3. Trace and Span Structure

Each RepoAgent run produces a structured trace hierarchy:

```
Root Trace: repoagent-run:<run_id>
 ├── Stage Span: stage:analyze
 │    └── Agent Span: agent:repo_planner:analyze
 │         ├── Tool Span: tool:repo_reader_list_tree
 │         └── Tool Span: tool:repo_reader_read_file
 ├── Stage Span: stage:code
 │    └── Agent Span: agent:coding_agent:code
 │         └── Tool Span: tool:apply_changeset_to_workspace
 ├── Stage Span: stage:test
 │    └── Tool Span: tool:sandbox_test_runner
 ├── Stage Span: stage:review
 │    └── Agent Span: agent:code_reviewer:review
 └── Stage Span: stage:open_pr
      └── Event: RUN_COMPLETED (or RUN_FAILED)
```

---

## 4. FastAPI Endpoint Documentation

The FastAPI application runs on port `8000` (`http://localhost:8000`).

### 4.1 POST `/runs`
Initiates a new autonomous coding run.

- **Request Body (`RunCreateRequest`):**
  ```json
  {
    "repo_url": "demo_repo",
    "requirement": "Add password reset functionality",
    "provider": "ollama",
    "max_retries": 3,
    "simulate_failures": 0,
    "create_pr": false,
    "use_live_agents": false,
    "sync": false
  }
  ```
- **Response (`201 Created`):**
  ```json
  {
    "run_id": "run_a1b2c3d4e5f6",
    "status": "running",
    "requirement": "Add password reset functionality",
    "repo_url": "D:\\Agentic AI\\demo_repo",
    "created_at": "2026-11-20T05:00:00Z",
    "message": "Run created and queued for execution"
  }
  ```
- **Error Responses:**
  - `400 Bad Request`: User requirement blocked by security guardrails (prompt injection detected) or invalid repository path.
  - `422 Unprocessable Entity`: Blank or malformed requirement string.

### 4.2 GET `/runs`
Lists all recorded runs.

- **Query Parameters:** `limit` (int, default: 50, range: 1–200).
- **Response (`200 OK`):**
  ```json
  [
    {
      "run_id": "run_a1b2c3d4e5f6",
      "status": "done",
      "requirement": "Add password reset functionality",
      "repo_url": "demo_repo",
      "started_at": "2026-11-20T05:00:00Z",
      "finished_at": "2026-11-20T05:00:15Z",
      "pr_url": "https://github.com/offline-test/repo/pull/run_a1",
      "error_count": 0
    }
  ]
  ```

### 4.3 GET `/runs/{run_id}`
Retrieves detailed status, test execution results, review report, and PR status.

- **Path Parameters:** `run_id` (string, alphanumeric).
- **Response (`200 OK`):**
  ```json
  {
    "run_id": "run_a1b2c3d4e5f6",
    "status": "done",
    "requirement": "Add password reset functionality",
    "repo_url": "demo_repo",
    "duration_s": 15.2,
    "retry_count": 0,
    "max_retries": 3,
    "test_reports": [
      {
        "command": "pytest tests/ -v",
        "passed": true,
        "exit_code": 0,
        "failed_tests": [],
        "duration_s": 1.5
      }
    ],
    "review": {
      "verdict": "approve",
      "summary": "Implementation satisfies requirement and tests pass.",
      "findings": []
    },
    "pr": {
      "title": "feat: Add password reset functionality",
      "url": "https://github.com/offline-test/repo/pull/run_a1",
      "branch": "repoagent/run_a1b2c3d4e5f6",
      "pull_number": 42
    },
    "trace_url": "https://cloud.langfuse.com/project/default/traces/trace_run_a1b2c3d4e5f6"
  }
  ```
- **Error Responses:**
  - `400 Bad Request`: Invalid `run_id` format (path traversal attempt).
  - `404 Not Found`: Unknown run ID.

### 4.4 GET `/runs/{run_id}/events`
Retrieves paginated audit event logs recorded in `runs/<run_id>/events.jsonl`.

- **Query Parameters:** `offset` (default: 0), `limit` (default: 50, max: 200).
- **Response (`200 OK`):**
  ```json
  {
    "run_id": "run_a1b2c3d4e5f6",
    "total_events": 14,
    "offset": 0,
    "limit": 50,
    "events": [
      {
        "timestamp": "2026-11-20T05:00:00Z",
        "run_id": "run_a1b2c3d4e5f6",
        "event_type": "RUN_STARTED",
        "stage": "lifecycle",
        "input_summary": { "requirement": "..." }
      }
    ]
  }
  ```

### 4.5 GET `/health`
Health check and component diagnostic endpoint.

- **Response (`200 OK`):**
  ```json
  {
    "status": "healthy",
    "version": "0.1.0",
    "runs_dir_writable": true,
    "langfuse_enabled": false,
    "timestamp": "2026-11-20T05:00:00Z"
  }
  ```

---

## 5. Streamlit Dashboard Overview

The monitoring dashboard is accessible on port `8501` (`http://localhost:8501`).

### Dashboard Sections
1. **Sidebar Diagnostics:** Displays FastAPI connection status, API version, and Langfuse connectivity.
2. **Section A (Run Overview):** Metrics counters for Total, Completed, Needs Human, Blocked, and Active runs.
3. **Section B (Execution Pipeline):** Visual stage indicators tracking progress through Understand, Plan, Code, Test, Review, and Promotion.
4. **Section C (Agent Activity):** Tabbed inspection of inputs, outputs, and findings for Google ADK Planner, CrewAI Coder, and Google ADK Reviewer.
5. **Section D & E (Verification & Tests):** Displays test exit codes, failure logs with safe redaction, DONE gate satisfaction, and PR link.
6. **Section F & G (Performance & Langfuse):** Execution duration, resource metrics, and direct link to Langfuse trace view.
7. **Audit Events Viewer:** Collapsible JSON viewer displaying paginated `events.jsonl` audit records.

---

## 6. Docker Service Architecture

The system is packaged into 4 containerized services managed via `docker-compose.yml`:

| Service | Port | Base Image | Non-Root User | Health Check | Purpose |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`repoagent-api`** | `8000` | `Dockerfile.api` | `appuser` (UID 1000) | `curl -f http://localhost:8000/health` | REST API backend |
| **`repoagent-dashboard`**| `8501` | `Dockerfile.dashboard` | `appuser` (UID 1000) | `curl -f http://localhost:8501/_stcore/health` | Streamlit UI |
| **`repo-planner`** | `8001` | `Dockerfile.agents` | `appuser` (UID 1000) | `curl -f http://localhost:8001/.well-known/agent-card.json` | ADK Planner A2A server |
| **`code-reviewer`** | `8002` | `Dockerfile.agents` | `appuser` (UID 1000) | `curl -f http://localhost:8002/.well-known/agent-card.json` | ADK Reviewer A2A server |

### Sandbox Security Policy Preservation
- Application containers run as unprivileged `appuser`.
- Test execution sandbox remains isolated (`network_mode="none"`).
- Docker socket (`/var/run/docker.sock`) is **never** mounted into application containers.
- Host `.env`, `.ssh`, and `.aws` directories are strictly excluded from mounts.
- Only `./runs` (read-write) and `./demo_repo` (read-only) are mounted.

---

## 7. Environment Configuration (Placeholders)

Configure the environment using `.env` or system environment variables:

```bash
# --- LLM Provider Configuration ---
REPOAGENT_PROVIDER=ollama
REPOAGENT_MODEL=qwen2.5-coder:3b
OPENAI_BASE_URL=http://localhost:11434/v1

# --- A2A Server URLs ---
REPOAGENT_PLANNER_URL=http://127.0.0.1:8001/
REPOAGENT_REVIEWER_URL=http://127.0.0.1:8002/

# --- Observability (Langfuse) ---
LANGFUSE_ENABLED=false
LANGFUSE_PUBLIC_KEY=pk-lf-placeholder-key
LANGFUSE_SECRET_KEY=sk-lf-placeholder-key
LANGFUSE_HOST=https://cloud.langfuse.com

# --- Workspace & Storage ---
REPOAGENT_RUNS_DIR=./runs
REPOAGENT_MAX_FILES_CHANGED=25
REPOAGENT_MAX_TOTAL_DIFF_BYTES=1000000

# --- Dashboard & API ---
REPOAGENT_API_URL=http://localhost:8000
```

---

## 8. Local Startup Instructions

### Option A: Local Process Startup (Development)

1. **Start ADK A2A Agent Servers (Terminal 1):**
   ```bash
   python -m repoagent.agents.adk.server --agent both --planner-port 8001 --reviewer-port 8002
   ```

2. **Start FastAPI Backend (Terminal 2):**
   ```bash
   uvicorn repoagent.api.app:app --host 0.0.0.0 --port 8000 --reload
   ```

3. **Start Streamlit Dashboard (Terminal 3):**
   ```bash
   streamlit run src/repoagent/ui/dashboard.py --server.port 8501
   ```

4. **Access Applications:**
   - FastAPI Docs: `http://localhost:8000/docs`
   - Streamlit Dashboard: `http://localhost:8501`

### Option B: Docker Compose Startup

```bash
docker compose up -d --build
```
Verify running containers:
```bash
docker compose ps
```

---

## 9. Health-Check Instructions

Verify system health via CLI:

```bash
# 1. FastAPI Backend Health
curl -s http://localhost:8000/health | python -m json.tool

# 2. ADK Planner A2A Card
curl -s http://localhost:8001/.well-known/agent-card.json | python -m json.tool

# 3. ADK Reviewer A2A Card
curl -s http://localhost:8002/.well-known/agent-card.json | python -m json.tool

# 4. Streamlit Health
curl -s http://localhost:8501/_stcore/health
```

---

## 10. Deployment Readiness and Remaining Approvals

### Cloud Readiness Summary
- Application components are completely containerized and stateless (relying on mounted persistent storage for `./runs`).
- Docker Compose configuration is modular and portable to container platforms (e.g. AWS ECS, GCP Cloud Run, Azure Container Apps, or Kubernetes).
- Non-root user permissions, health check endpoints, and secret isolation are production-ready.

### Mandatory User Approval Condition
In strict accordance with project constraints:
> **No cloud infrastructure has been provisioned, no paid resources have been created, and no secrets have been published.**
> Prior to actual cloud deployment, the proposed cloud provider (e.g. Google Cloud Run vs AWS ECS), architecture sizing, cost estimates, and necessary IAM permissions will be presented to the user for formal review and explicit approval.

---

## 11. Testing Evidence

### Test Execution Summary

| Test Suite | File | Tests | Result | Duration |
| :--- | :--- | :--- | :--- | :--- |
| **Langfuse Observability** | `tests/test_langfuse.py` | 8 | **8 PASSED** | 0.17s |
| **FastAPI Backend** | `tests/test_api.py` | 11 | **11 PASSED** | 1.00s |
| **Streamlit Dashboard** | `tests/test_dashboard.py` | 8 | **8 PASSED** | 0.29s |
| **Docker Configuration** | `tests/test_docker_config.py` | 6 | **6 PASSED** | 0.11s |
| **Central Event Logger** | `tests/test_event_logger.py` | 10 | **10 PASSED** | 1.49s |
| **End-to-End Security** | `tests/security/` | 70 | **70 PASSED** | 14.79s |
| **Demo Repository Tests** | `demo_repo/tests/` | 12 | **12 PASSED** | 0.04s |
| **Full Project Regression Suite** | `tests/ -k "not docker_sandbox_live"` | 280 | **278 PASSED** (2 deselected) | 40.13s |
| **Live Integration & E2E Run** | `scripts/verify_step8_live.py` | 10 | **10 PASSED** | ~17s |

All linters (`ruff check .`) and formatters (`ruff format --check .`) passed cleanly across all 81 project files.

---

## 12. Known Limitations & Environment Constraints

1. **Docker Desktop Engine Status:** The `docker` CLI is installed on this host (`C:\Users\Kushagra\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe`), but the Docker Desktop daemon is not currently active (`npipe:////./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified`). All compose configurations (`docker-compose.yml`, `docker-compose.langfuse.yml`) and Dockerfiles are validated statically via `docker compose config` (exit code 0) and unit tests. Live services and applications are tested and confirmed fully operational via native local process execution on ports 8000, 8001, 8002, and 8501.
2. **Local LLM Token & Cost Reporting:** Local LLM engines (e.g. Ollama with `qwen2.5-coder:3b`) do not reliably expose token counts or monetary cost metrics. RepoAgent strictly reports these metrics as `N/A` ($0.00 Local), never synthesizing fake values.
3. **Streamlit State Consumption:** The Streamlit dashboard operates strictly as a read-only observability consumer of the authoritative FastAPI/EventLogger state; it does not maintain an independent run state.
4. **Loopback Sockets on Windows Sandbox:** When running within standard Windows sandbox isolation, local loopback sockets can block during `ProactorEventLoop` initialization. Production execution and testing suites utilize bounded timeouts and bypass isolation for local network servers.

---

## 13. Step 8 Verification Matrix

| Verification Scope | Verification Mode | Target / Endpoint | Result | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **FastAPI Backend Health** | Live Local Process | `GET http://127.0.0.1:8000/health` | **PASS (200 OK)** | Status: healthy, v0.1.0, runs_dir_writable: true |
| **ADK Planner Agent Card** | Live Local Process | `GET http://127.0.0.1:8001/.well-known/agent-card.json` | **PASS (200 OK)** | Agent: `repo-planner`, skills: `analyze_repo`, `generate_plan` |
| **ADK Reviewer Agent Card** | Live Local Process | `GET http://127.0.0.1:8002/.well-known/agent-card.json` | **PASS (200 OK)** | Agent: `code-reviewer`, skill: `review_diff` |
| **Streamlit Dashboard Health** | Live Local Process | `GET http://127.0.0.1:8501/_stcore/health` | **PASS (200 OK)** | Headless server starts and responds 200 OK |
| **Realistic E2E Run Creation** | Live Local Process | `POST http://127.0.0.1:8000/runs` | **PASS (201 Created)** | Created `run_7f666f0ac742`, executed in background |
| **E2E Status & DONE Gate** | Live Local Process | `GET http://127.0.0.1:8000/runs/{run_id}` | **PASS (Status: DONE)** | Duration 2.74s, test report 12 passed in 0.08s, review approved |
| **E2E Audit Events** | Live Local Process | `GET http://127.0.0.1:8000/runs/{run_id}/events` | **PASS (6 events)** | RUN_STARTED, INPUT_ALLOWED, CHANGESET_PASSED, RUN_COMPLETED |
| **Target Repo Containment** | Live Local Process | `git status demo_repo/` | **PASS (Clean)** | Zero files modified in `demo_repo/` |
| **Langfuse Fail-Safe & Tracing** | Mock / Local Fixture | `tests/test_langfuse.py` | **PASS (8 tests)** | Root trace, agent/tool spans, secret redaction, fail-safe degradation |
| **Streamlit UI Components** | Mock / Live Client | `tests/test_dashboard.py` + Live Script | **PASS (8 tests)** | Sections A-I, metrics aggregation, log redaction & truncation |
| **Docker Compose Config** | Static Validation | `docker compose config` | **PASS (Code 0)** | 4 services, ports 8000, 8001, 8002, 8501, 4 healthchecks, non-root users |
| **Langfuse Compose Config** | Static Validation | `docker compose -f docker-compose.langfuse.yml config` | **PASS (Code 0)** | Langfuse Server v3 + PostgreSQL 16 on port 3000 |

---

## 14. Teacher Evaluation Criteria Status

| Criterion | Requirement | Implementation / Status |
| :--- | :--- | :--- |
| **1. Agentic Frameworks** | CrewAI + Google ADK only | **Fully Preserved** (Zero LangGraph, AutoGen, or LangChain agents) |
| **2. Multi-Agent Specialization** | Planner (ADK), Coder (CrewAI), Reviewer (ADK) | **Fully Preserved** (A2A JSON-RPC endpoints on 8001/8002) |
| **3. A2A Communication** | Standards-compliant Agent Cards & JSON-RPC | **Fully Preserved** (Verified live on `/well-known/agent-card.json`) |
| **4. MCP Integration** | MCP tools for repo reading and testing | **Fully Preserved** (Read-only repo reader, sandboxed test runner) |
| **5. Deterministic Verification** | Exit codes, failed test list, workspace hash | **Fully Preserved** (Recorded in TestReport & checked by reviewer) |
| **6. Atomic ChangeSets** | Validated byte-for-byte, rollback on failure | **Fully Preserved** (Enforced by ChangeSetValidator & Applier) |
| **7. Input Guardrails** | Screening requirements, A2A, and tool calls | **Fully Preserved** (Screening at API, server middleware, and callbacks) |
| **8. Central Event Logging** | Append-only JSONL with credential redaction | **Fully Preserved** (`runs/<run_id>/events.jsonl` + forwarded to Langfuse) |
| **9. Distributed Tracing** | Langfuse trace hierarchy & secret redaction | **Fully Implemented** (Fail-safe, no synthetic tokens/costs) |
| **10. DONE Gate & Idempotency** | Gated PR promotion, human review for flags | **Fully Preserved** (PR gated, human approval required) |
| **11. Safety & Containment** | Zero demo_repo mutations, non-root containers | **Fully Preserved** (`demo_repo/` untouched, Docker security verified) |
