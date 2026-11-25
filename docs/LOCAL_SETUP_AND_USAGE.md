# RepoAgent: Complete Local Setup and Usage Guide

Welcome to the complete local setup, execution, troubleshooting, and demonstration guide for **RepoAgent**.

This document is designed for students, developers, and reviewers who want to understand, run, and evaluate RepoAgent locally on Windows using PowerShell. Every command, path, and configuration mentioned in this guide reflects the exact, current implementation in this repository.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Project Structure](#2-project-structure)
3. [Prerequisites and Environment Setup](#3-prerequisites-and-environment-setup)
4. [First-Time Setup](#4-first-time-setup)
5. [Start and Stop Services](#5-start-and-stop-services)
6. [Learn the CLI](#6-learn-the-cli)
7. [Run the Existing REPLAY Demo](#7-run-the-existing-replay-demo)
8. [Add ANY New Feature Using a Natural-Language Requirement](#8-add-any-new-feature-using-a-natural-language-requirement)
9. [Live Model Workflow](#9-live-model-workflow)
10. [Testing and Quality Checks](#10-testing-and-quality-checks)
11. [GitHub PR Workflow](#11-github-pr-workflow)
12. [Run Artifacts and Logs](#12-run-artifacts-and-logs)
13. [Troubleshooting](#13-troubleshooting)
14. [Complete Demo Script](#14-complete-demo-script)
15. [Safety Checklist](#15-safety-checklist)
16. [Quick Reference](#16-quick-reference)

---

## 1. Project Overview

### What is RepoAgent?
**RepoAgent** is an autonomous, agentic coding assistant that transforms a high-level, natural-language software requirement into a verified, peer-reviewed, and production-ready GitHub Pull Request.

In traditional software development, adding a feature requires multiple steps:
1. Exploring the codebase and understanding component relationships.
2. Formulating an implementation plan.
3. Editing existing source code or writing new modules.
4. Writing automated tests to prevent regressions.
5. Executing test suites in an isolated environment.
6. Submitting the code for peer review.
7. Opening a structured Pull Request.

RepoAgent automates this entire lifecycle using a multi-agent architecture combining **CrewAI**, **Google Agent Development Kit (ADK)** over **Agent-to-Agent (A2A)** protocol, and **Model Context Protocol (MCP)** tool servers.

### End-to-End Workflow Architecture

```
                                  [ User Requirement ]
                                           │
                                           ▼
                       ┌───────────────────────────────────────┐
                       │   RepoAgentFlow (CrewAI Orchestrator) │
                       └───────────────────┬───────────────────┘
                                           │
                                           ▼
                                 Step 1: Analyze & Plan
                         (Google ADK Planner Agent over A2A:8001)
                         Uses: MCP repo_reader (list_tree, read_file)
                                           │
                                           ▼
                                 Step 2: Coding Loop
                             (CrewAI Coding & Testing Agents)
                          Applies ChangeSet -> Compiles via AST
                                           │
                                           ▼
                               Step 3: Test Verification
                      Deterministic Docker Sandbox (No Network, CPU/RAM Caps)
                          Runs pytest -> Produces TestReport
                                           │
                        ┌──────────────────┴──────────────────┐
                        │ Pass?                               │ Fail?
                        ▼                                     ▼
             Step 4: Code Review                    Correction Loop (Max 3 retries)
     (Google ADK Reviewer over A2A:8002)             Feeds error log back to Coder
                        │                                     │
           ┌────────────┴────────────┐                        ▼
           │ Approved?               │ Rejected?        Retries exhausted?
           ▼                         ▼                        ▼
     Step 5: DONE Gate         Retry (within cap)       Terminal: needs_human
(Deterministic Invariants)
           │
           ▼
     Step 6: PR Agent
(GitHub MCP Server via npx)
Preflight -> Commit -> Open PR -> Verify
```

### Normal Live Model Run vs. REPLAY Mode

| Dimension | Live Model Run (`--live`) | REPLAY Mode (`--replay`) |
|---|---|---|
| **Code Generation** | Live LLM (Ollama `qwen2.5-coder:3b` or Gemini) generates plan and diffs dynamically. | Deterministic reference solution loaded from fixture file (`tests/fixtures/replay_password_reset.json`). |
| **A2A Agents** | Calls ADK Planner (8001) and ADK Reviewer (8002) over HTTP A2A. | Calls ADK Planner and Reviewer over HTTP A2A using the reference diff and test reports. |
| **Sandbox Execution** | Real test execution inside isolated Docker container. | Real test execution inside isolated Docker container. |
| **Purpose** | Demonstrates autonomous reasoning, code editing, and error correction. | Provides reproducible, deterministic verification of the complete pipeline without LLM non-determinism. |

> [!IMPORTANT]
> **Known Limitation & Honest Framing**:
> In Step 5 and Step 6, the verified passing runs (`replay_4c73dac5` and `replay_f70d568d`) used the hand-written reference solution fixture to prove pipeline correctness, Docker sandbox isolation, A2A communication, and GitHub MCP integration.
> When tested live with local Ollama `qwen2.5-coder:3b`, the 3B model successfully modified application logic (`app.py`, `auth.py`) but failed to generate the required unit tests under `tests/`, correctly triggering the pipeline's strict correctness gate and terminating safely at `status=needs_human`. Never describe a REPLAY run as model-generated code.

---

## 2. Project Structure

Every path below exists in the repository and has a specific responsibility:

```
D:\Agentic AI\
├── .env.example                     # Template of all supported environment variables
├── pyproject.toml                   # Project dependencies and tool configurations
├── Dockerfile.agents                # Container definition for A2A agent services
├── docker-compose.yml               # Multi-container orchestration for Planner and Reviewer
├── LOCAL_SETUP_AND_USAGE.md         # This comprehensive user and setup manual
│
├── src/repoagent/                   # Main Python application package
│   ├── __init__.py                  # Package root and version metadata
│   ├── flow.py                      # Central CrewAI Flow orchestrator & state machine
│   ├── run.py                       # Primary CLI entry point
│   ├── schemas.py                   # Pydantic v2 domain schemas (RunState, TestReport, etc.)
│   ├── pr_agent.py                  # Step 6 PR Agent, GitHub MCP caller, & report writer
│   ├── a2a_client.py                # Client for Google ADK agents communicating over A2A
│   ├── mcp_client.py                # Client managing stdio connections to MCP servers
│   ├── changeset_applier.py         # Unified diff parser, AST validator, & patch applier
│   ├── workspace_utils.py           # Workspace cloner, git diff extractor, & run dir resolver
│   ├── fixtures.py                  # Fallback reference datasets
│   │
│   ├── agents/                      # Specialized agent implementations
│   │   ├── adk/                     # Google ADK agent servers
│   │   │   ├── planner.py           # Planner agent (analyzes repo and produces Plan)
│   │   │   ├── reviewer.py          # Reviewer agent (evaluates diff against plan & tests)
│   │   │   └── server.py            # Starlette/uvicorn HTTP server exposing A2A endpoints
│   │   └── crewai/                  # CrewAI task-based coding agents
│   │       ├── coding_agent.py      # Implements code modifications from plan & errors
│   │       └── testing_agent.py     # Writes tests and prepares test commands
│   │
│   ├── mcp_servers/                 # In-process stdio MCP servers
│   │   ├── repo_reader.py           # Exposes list_tree, read_file, search_code
│   │   └── test_runner.py           # Executes commands inside Docker sandbox
│   │
│   └── providers/                   # LLM provider abstraction layer
│       ├── base.py                  # Abstract base class LLMProvider
│       ├── factory.py               # Provider factory (get_provider)
│       ├── gemini.py                # Google Gemini adapter
│       ├── openai_compatible.py     # Ollama / vLLM adapter
│       └── retry.py                 # Exponential backoff and retry wrapper
│
├── scripts/                         # Operational and acceptance scripts
│   ├── start_agents.ps1             # PowerShell script to start A2A agents on 8001/8002
│   ├── start_agents.sh              # Bash equivalent for Linux/macOS
│   ├── run_coding_loop.py           # Alternate step-runner CLI with tee console logging
│   ├── demo_tools.py                # Standalone tool demo script
│   ├── smoke_test_provider.py       # LLM connectivity verification script
│   └── verify_step5_deliverables.py # Automated end-to-end verification script
│
├── tests/                           # Project automated test suite (131 tests)
│   ├── conftest.py                  # Pytest fixtures and runs/ tmp_path isolation fixture
│   ├── test_a2a.py                  # A2A client, agent card, and protocol tests
│   ├── test_changeset_applier.py    # Diff application and AST syntax validation tests
│   ├── test_coding_loop.py          # Correction loop logic and failure context tests
│   ├── test_flow.py                 # Orchestrator flow state transition tests
│   ├── test_mcp_tools.py            # Repo reader and test runner MCP tests
│   ├── test_pr_agent.py             # PR creation, verification, and scoping tests
│   ├── test_providers.py            # Provider factory and retry logic tests
│   ├── test_retry.py                # Retry mechanism unit tests
│   ├── test_schemas.py              # Pydantic v2 model validation tests
│   ├── test_verification_hardening.py# Done-gate and test-isolation hardening tests
│   └── fixtures/                    # Reference test fixtures
│       └── replay_password_reset.json # Reference solution for password reset
│
├── demo_repo/                       # Baseline target repository (Notes API)
│   ├── pyproject.toml               # Demo repo project dependencies
│   ├── src/notes_app/               # Application code (app.py, auth.py, models.py, notes.py)
│   └── tests/                       # 12 baseline unit tests (test_auth.py, test_notes.py)
│
├── sandbox/                         # Docker execution sandbox
│   └── Dockerfile                   # Python 3.11-slim container with pytest & ruff
│
├── runs/                            # Historical run artifacts (quarantined by run_id)
│   ├── replay_f70d568d/             # Step 6 verified live PR creation run
│   ├── replay_4c73dac5/             # Step 5 verified reference replay run
│   └── 7bb1fb173e81/                # Step 5 live Ollama run (gated at needs_human)
│
└── docs/                            # Architectural and evidence documentation
    ├── ARCHITECTURE.md              # System design diagrams and invariant definitions
    ├── EVIDENCE.md                  # Detailed audit log of all landmark runs
    └── PROGRESS.md                  # Step-by-step development history
```

---

## 3. Prerequisites and Environment Setup

### System Checklist
- **Operating System**: Windows 10 or 11 (64-bit).
- **Shell**: Windows PowerShell 5.1 or PowerShell 7+.
- **Python**: Python 3.11.x (exact project virtual environment is at `.venv\Scripts\python.exe`).
- **Container Runtime**: Docker Desktop for Windows (with Linux containers enabled and Docker daemon running).
- **Node.js & npm**: Node.js v18+ with `npx` (required only if running live GitHub MCP server for real PR creation).
- **Git**: Git for Windows installed and in `PATH`.
- **Local LLM (Optional for Live Runs)**: Ollama installed and serving `qwen2.5-coder:3b` locally on port 11434.

### Requirements Breakdown by Workflow Mode

| Component | Offline Tests / Stubs | REPLAY Demo | Live Model Run | Real GitHub PR |
|---|:---:|:---:|:---:|:---:|
| **Python 3.11 `.venv`** | Required | Required | Required | Required |
| **Docker Engine** | Not required for mocks | **Required** | **Required** | **Required** |
| **A2A Agents (8001, 8002)** | Not required for stubs | **Required** | **Required** | **Required** |
| **Ollama (`qwen2.5-coder:3b`)** | Not required | Not required | **Required** | Optional |
| **GitHub Personal Access Token** | Not required | Not required | Not required | **Required** |
| **`npx` (Node.js)** | Not required | Not required | Not required | **Required** |

### Environment Variables Overview (`.env`)

Create your `.env` file from the provided `.env.example`. Never commit `.env` to Git.

```bash
# LLM Provider Configuration
REPOAGENT_PROVIDER=ollama                # 'ollama' or 'gemini'
REPOAGENT_MODEL=qwen2.5-coder:3b         # Model name
OPENAI_BASE_URL=http://localhost:11434/v1 # Ollama OpenAI-compatible endpoint
OPENAI_API_KEY=dummy                     # Required non-empty string for local client
GOOGLE_API_KEY=                          # Required only if REPOAGENT_PROVIDER=gemini

# GitHub Integration (Required only for real PR creation)
GITHUB_TOKEN=ghp_yourPersonalAccessToken # Fine-grained or classic token
GITHUB_OWNER=YourGitHubUsername          # GitHub account/org owner
GITHUB_REPO=demo_repo                    # Target repository name

# Docker Sandbox Settings
SANDBOX_IMAGE=repoagent-sandbox:latest   # Built from sandbox/Dockerfile
SANDBOX_TIMEOUT_S=120                    # Container timeout cap
SANDBOX_CPU_LIMIT=1.0                    # Max CPU cores
SANDBOX_MEMORY_LIMIT=512m                # Max RAM allocation
```

---

## 4. First-Time Setup

Run the following commands in Windows PowerShell.

### Step 4.1: Navigate to Project Directory
```powershell
Set-Location -Path "D:\Agentic AI"
```

### Step 4.2: Verify Python and Virtual Environment
RepoAgent requires Python 3.11. A dedicated virtual environment already exists in `.venv`.
```powershell
# Verify Python version inside .venv
.\.venv\Scripts\python.exe --version
# Expected output: Python 3.11.x
```

### Step 4.3: Verify Project Installation
Verify that `repoagent` is installed in editable mode without broken dependencies:
```powershell
.\.venv\Scripts\python.exe -m pip check
# Expected output: No broken requirements found.
```

If you ever need to re-install in editable mode with development dependencies:
```powershell
.\.venv\Scripts\pip.exe install -e ".[dev]"
```

### Step 4.4: Verify Docker Availability
Ensure Docker Desktop is running and reachable:
```powershell
docker info --format '{{.ServerVersion}}'
```
Build the sandbox Docker image if it is not already built:
```powershell
docker build -t repoagent-sandbox:latest -f sandbox/Dockerfile sandbox/
```

### Step 4.5: Verify Git Working Tree
Confirm your repository is clean and on the latest commit:
```powershell
git status --short
git log -1 --oneline
git tag --list "step-*"
```

### Step 4.6: Verify Required Ports Are Free
Ensure ports 8001 and 8002 are not occupied by stale processes:
```powershell
Get-NetTCPConnection -LocalPort 8001,8002 -ErrorAction SilentlyContinue
# If nothing is returned, the ports are free.
```

---

## 5. Start and Stop Services

RepoAgent uses two background services for Google ADK agents:
- **Planner Agent**: runs on `http://127.0.0.1:8001`
- **Reviewer Agent**: runs on `http://127.0.0.1:8002`

### Method A: Start Both Agents Concurrently (Recommended)

Open a **separate Windows PowerShell window** and run:
```powershell
Set-Location -Path "D:\Agentic AI"
.\scripts\start_agents.ps1
```

Or invoke the Python module directly:
```powershell
.\.venv\Scripts\python.exe -m repoagent.agents.adk.server --agent all --host 127.0.0.1 --planner-port 8001 --reviewer-port 8002 --workspace demo_repo
```

### Method B: Verify Service Endpoints

From your main PowerShell window, query the Agent Cards:
```powershell
# Check Planner Agent Card
Invoke-RestMethod -Uri "http://127.0.0.1:8001/.well-known/agent-card.json" | Select-Object name, version

# Check Reviewer Agent Card
Invoke-RestMethod -Uri "http://127.0.0.1:8002/.well-known/agent-card.json" | Select-Object name, version
```

### Method C: Stopping Services

1. In the PowerShell window where `start_agents.ps1` is running, press **Ctrl + C**.
2. If an agent process remains running in the background, identify and terminate it:
```powershell
# Find process IDs listening on 8001 or 8002
$conns = Get-NetTCPConnection -LocalPort 8001,8002 -ErrorAction SilentlyContinue
if ($conns) {
    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
    Write-Host "Found active processes on ports 8001/8002: $pids"
    # To stop cleanly: Stop-Process -Id $pids -Force
}
```

---

## 6. Learn the CLI

RepoAgent provides two CLI entry points for different workflows:

### CLI Comparison

| Feature | `python -m repoagent.run` | `python scripts/run_coding_loop.py` |
|---|---|---|
| **Role** | Primary CLI entry point. | Alternate acceptance runner. |
| **Output** | Structured JSON dump of final `RunState`. | Console output teed to `runs/<run_id>/console.log`. |
| **Workspace Handling** | Automatically creates isolated workspace at `runs/<run_id>/workspace`. | Clones `demo_repo` into `runs/<run_id>/workspace` and checks out branch. |
| **Replay Flag** | `--replay` | `--replay` |
| **PR Creation Flag** | `--create-pr` | `--create-pr` |

### Primary CLI Flag Reference (`python -m repoagent.run`)

```powershell
.\.venv\Scripts\python.exe -m repoagent.run --help
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--requirement` | string | **Required** | The natural-language requirement to implement. |
| `--repo` | string | `demo_repo` | Path to target repository. |
| `--provider` | string | `None` (uses `.env`) | Override LLM provider (`gemini` or `openai_compatible`). |
| `--max-retries` | int | `3` | Maximum correction iterations on test failure. |
| `--simulate-failures` | int | `0` | Simulates test failures for N rounds to test retry limits. |
| `--live` | flag | `True` | Uses live LLM agents. |
| `--stub` | flag | `False` | Uses deterministic stubs (for fast testing without LLM). |
| `--replay` | flag | `False` | Activates REPLAY mode with reference solution fixture. |
| `--create-pr` | flag | `False` | Activates real GitHub PR creation if DONE gate passes. |

---

## 7. Run the Existing REPLAY Demo

The REPLAY demo applies the verified, hand-written reference solution for password reset, executes tests in the Docker sandbox, runs A2A review, and verifies the pipeline without non-deterministic LLM hallucinations.

### Step 7.1: Preflight Verification
1. Ensure Docker Desktop is active.
2. Ensure A2A agent services are running on ports 8001 and 8002 (see [Section 5](#5-start-and-stop-services)).

### Step 7.2: Run Command (Safe Offline Mode)
Run the replay demo **without** `--create-pr` to verify everything locally without touching GitHub:
```powershell
.\.venv\Scripts\python.exe scripts/run_coding_loop.py --replay
```

### Step 7.3: Expected Console Output
1. Applies reference solution fixture to `runs/replay_<id>/workspace`.
2. Starts Docker sandbox container and executes `pytest`.
3. Passes **15 items** (12 baseline + 3 new tests) in ~0.25 seconds:
   - `test_request_password_reset_success`
   - `test_reset_password_with_valid_token`
   - `test_reset_password_with_invalid_token`
4. Requests review over A2A on port 8002.
5. Receives `approve` verdict from Code Reviewer.
6. Emits `report.json` and `report.md`.
7. Concludes with:
   ```text
   [SUCCESS] Run ended with passing tests on the branch!
   ```

### Step 7.4: Verify Local Integrity
Verify that baseline `demo_repo/` remained 100% untouched:
```powershell
git status --short demo_repo/
# Output must be empty
```

---

## 8. Add ANY New Feature Using a Natural-Language Requirement

This section explains how to formulate and submit completely new features beyond password reset.

### Recommended Natural-Language Feature Examples

Here are 5 concrete features ready to submit to RepoAgent:

#### Example 1: Email Format Validation
- **Requirement**: `"Add email format validation in AuthService.register. If the email does not contain '@' or a valid domain, raise a ValueError with 'Invalid email address'. Add tests for valid and invalid emails in tests/test_auth.py."`

#### Example 2: Note Search by Keyword
- **Requirement**: `"Add search_notes(user_id, query) method to NotesService and NotesApp that returns all notes whose title or content contains the query string (case-insensitive). Add comprehensive unit tests in tests/test_notes.py."`

#### Example 3: Pagination for Notes List
- **Requirement**: `"Update get_notes in NotesService and NotesApp to accept optional 'limit' and 'offset' integer parameters for pagination, with default limit=10 and offset=0. Add unit tests in tests/test_notes.py."`

#### Example 4: Note Archival Feature
- **Requirement**: `"Add archive_note(user, note_id) method that marks a note as archived. Update get_notes to exclude archived notes by default unless include_archived=True. Add unit tests in tests/test_notes.py."`

#### Example 5: User Password Complexity Check
- **Requirement**: `"Enforce password complexity in AuthService.register: require minimum 8 characters, at least one digit, and at least one uppercase letter. Raise ValueError('Weak password') if unsatisfied. Update tests/test_auth.py with test cases."`

### Command Syntax in PowerShell

Always enclose natural-language requirements in **double quotes** when executing from Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m repoagent.run `
    --repo demo_repo `
    --requirement "Add email format validation in AuthService.register. Raise ValueError for invalid format and add tests in tests/test_auth.py." `
    --max-retries 3
```

### The 9-Step Verification Lifecycle

1. **Baseline Inspection**: RepoAgent computes the pristine baseline hash of `demo_repo`.
2. **Workspace Isolation**: A fresh, isolated copy is created in `runs/<run_id>/workspace`. The original `demo_repo` is never modified directly.
3. **Planning**: ADK Planner inspects the workspace via MCP `repo_reader` and generates a structured `Plan`.
4. **Code Generation**: Coding agent writes diffs; `changeset_applier` validates syntax with Python AST before writing to disk.
5. **Deterministic Sandbox Testing**: Docker sandbox runs `pytest`. Test output, exit code, and new workspace hash are recorded.
6. **Correction Loop**: If tests fail, failure logs and failing test nodes are fed back to the Coder for up to 3 retry iterations.
7. **Code Review**: If tests pass, ADK Reviewer evaluates the diff against the plan and test report over A2A.
8. **DONE Gate Verification**: The orchestrator enforces strict invariants before declaring success.
9. **Report Writing**: Generates `report.json` and `report.md` capturing the entire trajectory.

### Understanding Run Statuses

- **`status: done`**: All tests passed in Docker sandbox, workspace hash changed from baseline, and Reviewer approved.
- **`status: needs_human`**: Maximum retry budget was reached without all tests passing, or DONE gate invariants were not satisfied.
- **`status: failed`**: An unhandled execution error occurred.

---

## 9. Live Model Workflow

To run RepoAgent with live LLM code generation using a local model:

### Step 9.1: Configure Environment
Ensure your `.env` contains:
```ini
REPOAGENT_PROVIDER=ollama
REPOAGENT_MODEL=qwen2.5-coder:3b
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=dummy
```

### Step 9.2: Verify Ollama Status
Check that Ollama is serving the required model:
```powershell
Invoke-RestMethod -Uri "http://localhost:11434/api/tags" | Select-Object -ExpandProperty models | Select-Object name
```

### Step 9.3: Execute Live Run
```powershell
.\.venv\Scripts\python.exe -m repoagent.run `
    --repo demo_repo `
    --requirement "Add search_notes(user_id, query) to NotesService and test it in tests/test_notes.py" `
    --max-retries 3
```

> [!WARNING]
> **Model Behavior Note**:
> Smaller coding models like `qwen2.5-coder:3b` may struggle to generate both the full implementation and the corresponding unit tests in a single shot. In verified testing, if the model omits test additions, RepoAgent deterministically catches the omission (`verification:missing_feature_tests`), increments the retry counter, and terminates safely at `needs_human`. This is the intended guardrail working as designed.

---

## 10. Testing and Quality Checks

### Run All Test Suites

Execute the automated test suites using the project virtualenv:

```powershell
# 1. Run full RepoAgent test suite (131 tests)
.\.venv\Scripts\python.exe -m pytest -v tests/

# 2. Run target demo application test suite (12 tests)
.\.venv\Scripts\python.exe -m pytest -v demo_repo/tests/

# 3. Run a specific test module
.\.venv\Scripts\python.exe -m pytest -v tests/test_pr_agent.py

# 4. Run Ruff linting check (55 files)
.\.venv\Scripts\ruff.exe check src tests demo_repo

# 5. Run Ruff formatting check (55 files)
.\.venv\Scripts\ruff.exe format --check src tests demo_repo
```

### Test Isolation Hygiene
All test suites use a temporary directory fixture (`conftest.py`) setting `REPOAGENT_RUNS_DIR` to `tmp_path`. Running `pytest` will **never** create folders inside the real `runs/` directory.

You can verify this invariant before and after testing:
```powershell
(Get-ChildItem -Directory runs).Count
```

---

## 11. GitHub PR Workflow

Step 6 implemented automated GitHub Pull Request creation and verification using the official GitHub MCP server.

### Safety Invariants
- **Default-Off Gate**: PR creation is strictly disabled by default. It activates only when `--create-pr` is explicitly passed or `REPOAGENT_LIVE_PR=true` is set.
- **DONE Gate Prerequisite**: PR creation is only attempted if `status == RunStatus.DONE` (tests passed deterministically, workspace modified, reviewer approved).
- **Baseline Preflight Check**: Compares remote `main` branch files against workspace baseline before creating branches.
- **Strict Push Scoping**: Only workspace files modified by the feature are pushed. Framework files, `.env`, `runs/`, and `.pyc` files are strictly excluded.
- **Never Writes to `main`**: All work is committed to branch `repoagent/<run_id>`. RepoAgent never merges PRs.

### Safe Offline Execution (Default)
When `--create-pr` is omitted, RepoAgent produces a local deterministic PR summary and reports, but touches no remote branches and opens no PRs:
```powershell
.\.venv\Scripts\python.exe scripts/run_coding_loop.py --replay
```

### Live PR Creation Execution (Requires Credentials)

> [!CAUTION]
> Running this command will open a real Pull Request on GitHub under your repository.

Prerequisites:
1. `GITHUB_TOKEN`, `GITHUB_OWNER`, and `GITHUB_REPO` set in `.env`.
2. `npx` available in `PATH`.

```powershell
.\.venv\Scripts\python.exe scripts/run_coding_loop.py --replay --create-pr
```

Upon success, the real PR URL is printed:
```text
https://github.com/<owner>/demo_repo/pull/<number>
```

---

## 12. Run Artifacts and Logs

Every execution creates a self-contained directory under `runs/<run_id>/`:

```
runs/<run_id>/
├── report.json         # Complete, machine-readable JSON snapshot of RunState
├── report.md           # Markdown summary with plan, changes, tests, review & PR
├── run_state.json      # Periodic state checkpoint
├── a2a.jsonl           # Protocol audit log of all A2A agent messages and tool calls
├── console.log         # Complete teed standard output and error stream
├── sandbox_raw_1.txt   # Raw stdout and stderr from Docker sandbox test attempt
├── test_reports/       # Detailed test result files
└── workspace/          # Quarantined git repository clone where code was executed
```

### Inspecting Runs with PowerShell

```powershell
# List the 5 most recent runs
Get-ChildItem -Directory runs | Sort-Object LastWriteTime -Descending | Select-Object -First 5 Name, LastWriteTime

# Inspect the markdown report of a run
Get-Content "runs\replay_f70d568d\report.md"

# Inspect the JSON report of a run
Get-Content "runs\replay_f70d568d\report.json" | ConvertFrom-Json | Select-Object run_id, requirement, status, pr
```

---

## 13. Troubleshooting

### 1. Port 8001 or 8002 Already in Use
- **Symptom**: `OSError: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8001)`
- **Diagnostic Command**:
  ```powershell
  Get-NetTCPConnection -LocalPort 8001,8002 -ErrorAction SilentlyContinue | Format-Table LocalPort, OwningProcess, State
  ```
- **Fix**: Terminate the specific orphan process holding the port:
  ```powershell
  Stop-Process -Id <PID> -Force
  ```

### 2. Docker Daemon Not Running
- **Symptom**: `docker.errors.DockerException: Error while fetching server API version`
- **Diagnostic Command**:
  ```powershell
  docker ps
  ```
- **Fix**: Launch Docker Desktop from the Start menu and wait until the status displays "Engine running".

### 3. Ollama Connection Refused
- **Symptom**: `httpx.ConnectError: [Errno 10061] No connection could be made because the target machine actively refused it`
- **Diagnostic Command**:
  ```powershell
  Invoke-RestMethod -Uri "http://localhost:11434/api/version"
  ```
- **Fix**: Start the Ollama application or run `ollama serve` in a terminal.

### 4. GitHub MCP Error: `GITHUB_TOKEN required`
- **Symptom**: `OSError: GITHUB_TOKEN environment variable is required for the GitHub MCP server.`
- **Fix**: Ensure your `.env` contains a valid `GITHUB_TOKEN` before invoking with `--create-pr`.

### 5. Flow Terminates with `needs_human`
- **Symptom**: Exit code 1, `status: needs_human`.
- **Diagnosis**: This is normal when a generated implementation fails tests 3 times in a row. Inspect `runs/<run_id>/report.md` to see the exact test failure logs and AST errors.

---

## 14. Complete Demo Script

Follow this script to demonstrate RepoAgent step-by-step to an audience or professor:

### Step 1: Preflight Verification (Show Clean Baseline)
```powershell
Set-Location -Path "D:\Agentic AI"
git status --short
# Show that working tree is clean and baseline demo_repo is untouched
```

### Step 2: Show Test Suite Passing
```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/
# Show: 131 passed
```

### Step 3: Start A2A Agent Services
In a second PowerShell window:
```powershell
.\scripts\start_agents.ps1
```
In your main window, verify the agents respond:
```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8001/.well-known/agent-card.json" | Select-Object name
Invoke-RestMethod -Uri "http://127.0.0.1:8002/.well-known/agent-card.json" | Select-Object name
```

### Step 4: Run the Password Reset REPLAY Demonstration
```powershell
.\.venv\Scripts\python.exe scripts/run_coding_loop.py --replay
```
Explain to your audience:
- Docker sandbox runs in complete isolation.
- Hand-written reference solution fixture is applied and verified.
- 15 pytest items pass in the sandbox container.
- ADK Reviewer evaluates diff over A2A and returns `approve`.
- Run finishes with `status: done`.

### Step 5: Show the Generated Reports
```powershell
$latestRun = (Get-ChildItem -Directory runs | Sort-Object LastWriteTime -Descending | Select-Object -First 1).Name
Get-Content "runs\$latestRun\report.md"
```

### Step 6: Show the Real GitHub PR (Historical Evidence)
Display the real Pull Request created during Step 6:
- URL: **https://github.com/Kushagra-Pachauri/demo_repo/pull/1**
- Show that only 3 files were changed (`app.py`, `auth.py`, `test_auth.py`).
- Show that zero RepoAgent internal files were leaked.

### Step 7: Clean Shutdown
Close the agent window or press **Ctrl + C**.

---

## 15. Safety Checklist

Before running any RepoAgent task, verify these rules:

1. **Never write to `main`**: All features must be developed on `repoagent/<run_id>` branches.
2. **Never merge automatically**: A human must review and merge any PR.
3. **Never commit `.env`**: Secret tokens must remain in local untracked `.env` files.
4. **Keep `demo_repo/` pristine**: Always ensure `git status --short demo_repo/` is clean before and after runs.
5. **Honest Attribution**: Always distinguish reference replay runs from live model generations.
6. **Isolated Execution**: All user-generated code must execute inside Docker containers, never on the host machine.

---

## 16. Quick Reference

Copy-paste cheatsheet for common daily tasks:

### Navigation & Environment
```powershell
Set-Location -Path "D:\Agentic AI"
.\.venv\Scripts\python.exe --version
```

### Start Services (Window 1)
```powershell
.\scripts\start_agents.ps1
```

### Run REPLAY Demo Safely (Window 2)
```powershell
.\.venv\Scripts\python.exe scripts/run_coding_loop.py --replay
```

### Run New Feature with Live Model (Window 2)
```powershell
.\.venv\Scripts\python.exe -m repoagent.run `
    --repo demo_repo `
    --requirement "Add email validation in AuthService.register" `
    --max-retries 3
```

### Run Fast Deterministic Stubs (Window 2)
```powershell
.\.venv\Scripts\python.exe -m repoagent.run `
    --repo demo_repo `
    --requirement "Add password reset" `
    --stub
```

### Run All Tests & Linters
```powershell
.\.venv\Scripts\python.exe -m pytest tests/
.\.venv\Scripts\python.exe -m pytest demo_repo/tests/
.\.venv\Scripts\ruff.exe check src tests demo_repo
.\.venv\Scripts\ruff.exe format --check src tests demo_repo
```

### Inspect Latest Report
```powershell
$r = (Get-ChildItem -Directory runs | Sort-Object LastWriteTime -Descending | Select-Object -First 1).Name
Get-Content "runs\$r\report.md"
```

### Stop Services
Press `Ctrl + C` in the agent window, or run:
```powershell
$p = (Get-NetTCPConnection -LocalPort 8001,8002 -ErrorAction SilentlyContinue).OwningProcess | Select-Object -Unique
if ($p) { Stop-Process -Id $p -Force }
```
