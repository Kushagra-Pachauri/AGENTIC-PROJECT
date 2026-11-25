"""Authenticated live execution gateway for RepoAgent.

Runs on host Python 3.11 with access to Docker engine, ADK agents, and Ollama.
Exposes secure endpoints for public Streamlit frontend over HTTPS tunnel.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Header, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Load environment
ROOT = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(ROOT / ".env")

# Set host execution environment defaults
MODEL_NAME = "qwen2.5-coder:3b"
OLLAMA_URL = "http://localhost:11434/v1"

os.environ["REPOAGENT_PROVIDER"] = "ollama"
os.environ["REPOAGENT_MODEL"] = MODEL_NAME
os.environ["OPENAI_MODEL"] = MODEL_NAME
os.environ["LOCAL_MODEL"] = MODEL_NAME
os.environ["OPENAI_BASE_URL"] = OLLAMA_URL
os.environ["REPOAGENT_PLANNER_URL"] = "http://127.0.0.1:8011"
os.environ["REPOAGENT_REVIEWER_URL"] = "http://127.0.0.1:8012"
os.environ["REPOAGENT_ALLOW_HOST_DOCKER"] = "1"

# Shared Gateway Auth Token
AUTH_TOKEN = os.environ.get("REPOAGENT_AUTH_TOKEN", "repoagent-live-token-2026")

logger = logging.getLogger("live_gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(
    title="RepoAgent Live Gateway",
    description="Secure execution gateway connecting public UI to local Docker sandbox and agents",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory rate limiting: IP -> list of timestamps
_RATE_LIMITS: dict[str, list[float]] = defaultdict(list)
_RUNNING_FLOWS: dict[str, Any] = {}
_LOCK = threading.Lock()
_A2A_SERVERS_STARTED = False


# Sanitization patterns for public gateway responses
_SECRET_PATTERNS = [
    (re.compile(r"ghp_[A-Za-z0-9_]{36,}", re.IGNORECASE), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{82}", re.IGNORECASE), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE), "[REDACTED_API_KEY]"),
    (re.compile(r"gsk_[A-Za-z0-9_-]{20,}", re.IGNORECASE), "[REDACTED_GROQ_KEY]"),
    (re.compile(r"[a-zA-Z]:\\[^\"'\n\r\t]+?[\\/]Agentic AI", re.IGNORECASE), "/workspace/repoagent"),
    (re.compile(r"[a-zA-Z]:\\[^\"'\n\r\t]+?[\\/]runs", re.IGNORECASE), "/workspace/runs"),
    (re.compile(r"C:\\Users\\[a-zA-Z0-9_.]+", re.IGNORECASE), "/home/developer"),
    (re.compile(r"D:\\Agentic AI", re.IGNORECASE), "/workspace"),
]


def sanitize_data(data: Any) -> Any:
    """Recursively sanitize paths, keys, and internal info."""
    if isinstance(data, str):
        for pat, rep in _SECRET_PATTERNS:
            data = pat.sub(rep, data)
        return data
    elif isinstance(data, list):
        return [sanitize_data(x) for x in data]
    elif isinstance(data, dict):
        return {k: sanitize_data(v) for k, v in data.items()}
    return data


def ensure_a2a_servers():
    """Ensure local ADK Planner (8011) and Reviewer (8012) servers are running."""
    global _A2A_SERVERS_STARTED
    with _LOCK:
        if _A2A_SERVERS_STARTED:
            return

        import httpx
        planner_ok = False
        try:
            r = httpx.get("http://127.0.0.1:8011/health", timeout=1.0)
            if r.status_code == 200:
                planner_ok = True
        except Exception:
            pass

        if not planner_ok:
            try:
                import uvicorn
                from repoagent.agents.adk.server import create_planner_app, create_reviewer_app
                from repoagent.providers.openai_compatible import OpenAICompatibleProvider

                provider = OpenAICompatibleProvider(model_name=MODEL_NAME, base_url=OLLAMA_URL)
                adk_model = provider.get_adk_llm()

                planner_app = create_planner_app(
                    host="127.0.0.1", port=8011, workspace="demo_repo", model=adk_model
                )
                p_cfg = uvicorn.Config(planner_app, host="127.0.0.1", port=8011, log_level="warning")
                p_srv = uvicorn.Server(p_cfg)
                threading.Thread(target=p_srv.run, daemon=True).start()

                reviewer_app = create_reviewer_app(host="127.0.0.1", port=8012, model=adk_model)
                r_cfg = uvicorn.Config(reviewer_app, host="127.0.0.1", port=8012, log_level="warning")
                r_srv = uvicorn.Server(r_cfg)
                threading.Thread(target=r_srv.run, daemon=True).start()

                time.sleep(1.5)
                logger.info("Local ADK Planner (8011) and Reviewer (8012) started successfully.")
            except Exception as e:
                logger.error("Failed to start background A2A servers: %s", e)

        _A2A_SERVERS_STARTED = True


def verify_bearer_token(authorization: str | None = Header(default=None)) -> None:
    """Verify authorization token header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Bearer token required.",
        )
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'.",
        )
    token = parts[1]
    if token != AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid access token. Remote execution unauthorized.",
        )


def check_rate_limit(request: Request) -> None:
    """Check IP rate limiting: max 5 requests per 60 seconds."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    with _LOCK:
        history = _RATE_LIMITS[client_ip]
        # Keep timestamps in the last 60 seconds
        history = [t for t in history if now - t < 60.0]
        if len(history) >= 5:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Maximum 5 execution requests per minute.",
            )
        history.append(now)
        _RATE_LIMITS[client_ip] = history


class CreateLiveRunPayload(BaseModel):
    requirement: str = Field(..., min_length=5, max_length=2000)
    repo: str = Field(default="demo_repo")
    max_retries: int = Field(default=2, ge=1, le=3)
    create_pr: bool = Field(default=False)
    sync: bool = Field(default=False)


def _execute_run_thread(flow: Any, run_id: str, run_dir: Path):
    """Background thread executing RepoAgentFlow safely."""
    try:
        logger.info("Starting live execution for run %s...", run_id)
        flow.kickoff()
        logger.info("Live execution for run %s finished with status: %s", run_id, flow.state.status)
    except Exception as exc:
        logger.error("Error executing run %s: %s", run_id, exc)
        flow.state.errors.append(f"Execution error: {exc}")
        flow.state.status = "needs_human"
    finally:
        with _LOCK:
            _RUNNING_FLOWS.pop(run_id, None)
        # Ensure final state checkpoint and report written
        try:
            flow._save_checkpoint()
        except Exception:
            pass


@app.get("/health")
@app.get("/api/v1/health")
def health_check():
    """Public health endpoint reporting execution host status."""
    return {
        "status": "healthy",
        "service": "repoagent-live-gateway",
        "version": "0.1.0",
        "provider": "ollama",
        "model": MODEL_NAME,
        "docker_available": True,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.post("/api/v1/runs", status_code=status.HTTP_201_CREATED)
def create_live_run(
    payload: CreateLiveRunPayload,
    request: Request,
    background_tasks: BackgroundTasks,
    _auth: None = Depends(verify_bearer_token),
):
    """Authenticate and initiate a live RepoAgent execution pipeline."""
    check_rate_limit(request)

    # 1. Validate repository constraint: STRICTLY demo_repo only
    if payload.repo != "demo_repo":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository restricted to 'demo_repo' for secure sandboxed execution.",
        )

    # 2. Input Security Screening (prompt injection, command execution)
    from repoagent.security import GuardrailDecision, screen_user_requirement

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    screening = screen_user_requirement(payload.requirement, run_id=run_id)
    if screening.decision == GuardrailDecision.BLOCK:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": f"Requirement blocked by security guardrails: {screening.reason}",
                "category": screening.category.value,
            },
        )

    # 3. Setup isolated workspace
    from repoagent.workspace_utils import get_runs_dir, setup_workspace

    runs_dir = get_runs_dir()
    run_dir = runs_dir / run_id
    isolated_workspace = run_dir / "workspace"

    source_repo = ROOT / "demo_repo"
    if not source_repo.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Baseline demo_repo not found on execution host.",
        )

    setup_workspace(source_repo, isolated_workspace)

    # 4. Ensure background ADK Planner & Reviewer are running
    ensure_a2a_servers()

    # 5. Instantiate RunState and Flow
    from repoagent.flow import RepoAgentFlow
    from repoagent.schemas import RunState, RunStatus

    initial_status = (
        RunStatus.NEEDS_HUMAN if screening.decision == GuardrailDecision.FLAG else RunStatus.RUNNING
    )
    state = RunState(
        run_id=run_id,
        requirement=payload.requirement,
        repo_url=str(isolated_workspace),
        max_retries=payload.max_retries,
        status=initial_status,
        is_live_execution=True,
    )

    flow = RepoAgentFlow(
        initial_state=state,
        workspace=isolated_workspace,
        use_live_agents=True,
        create_pr=payload.create_pr,
    )
    flow._save_checkpoint()

    with _LOCK:
        _RUNNING_FLOWS[run_id] = flow

    if payload.sync:
        _execute_run_thread(flow, run_id, run_dir)
    else:
        background_tasks.add_task(_execute_run_thread, flow, run_id, run_dir)

    return {
        "run_id": run_id,
        "status": flow.state.status.value if hasattr(flow.state.status, "value") else str(flow.state.status),
        "requirement": payload.requirement,
        "created_at": datetime.now(UTC).isoformat(),
        "message": "Live RepoAgent run initiated in isolated host Docker sandbox.",
    }


@app.get("/api/v1/runs")
def list_runs(limit: int = Query(default=20, ge=1, le=50)):
    """List runs with sanitized paths and sensitive data removed."""
    from repoagent.workspace_utils import get_runs_dir

    runs_dir = get_runs_dir()
    if not runs_dir.exists():
        return []

    results = []
    run_paths = sorted(
        [p for p in runs_dir.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    for p in run_paths[:limit]:
        state_file = p / "report.json"
        if not state_file.is_file():
            state_file = p / "run_state.json"
        if state_file.is_file():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8", errors="ignore"))
                results.append(
                    sanitize_data({
                        "run_id": data.get("run_id", p.name),
                        "status": data.get("status", "unknown"),
                        "requirement": data.get("requirement", ""),
                        "duration_s": data.get("duration_s"),
                        "created_at": data.get("created_at") or data.get("started_at"),
                        "is_live_execution": data.get("is_live_execution", True),
                        "pr": data.get("pr"),
                    })
                )
            except Exception:
                pass

    return results


@app.get("/api/v1/runs/{run_id}")
def get_run_details(run_id: str):
    """Retrieve detailed state for a specific run."""
    from repoagent.workspace_utils import get_runs_dir

    run_dir = get_runs_dir() / run_id
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="Run not found.")

    for fn in ("report.json", "run_state.json"):
        sf = run_dir / fn
        if sf.is_file():
            try:
                data = json.loads(sf.read_text(encoding="utf-8", errors="ignore"))
                return sanitize_data(data)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to read run state: {e}")

    raise HTTPException(status_code=404, detail="Run state file missing.")


@app.get("/api/v1/runs/{run_id}/events")
def get_run_events(run_id: str, limit: int = 100):
    """Retrieve audit events for a run."""
    from repoagent.workspace_utils import get_runs_dir

    events_file = get_runs_dir() / run_id / "events.jsonl"
    if not events_file.is_file():
        return []

    events = []
    try:
        lines = events_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in lines[-limit:]:
            if line.strip():
                events.append(sanitize_data(json.loads(line)))
    except Exception:
        pass

    return events
