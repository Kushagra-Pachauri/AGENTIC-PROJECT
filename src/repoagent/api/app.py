"""FastAPI backend application for RepoAgent execution, status monitoring, and event retrieval.

Provides:
- POST /runs: Initiate an autonomous coding run.
- GET /runs: List existing runs.
- GET /runs/{run_id}: Retrieve detailed run status and findings.
- GET /runs/{run_id}/events: Retrieve paginated audit event logs.
- GET /health: Health check and diagnostic status.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from repoagent.api.schemas import (
    HealthResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunEventsResponse,
    RunResponse,
    RunSummary,
    validate_run_id_safe,
)
from repoagent.flow import RepoAgentFlow
from repoagent.observability import get_langfuse_tracker, redact_text_for_tracing
from repoagent.schemas import RunState, RunStatus
from repoagent.security import GuardrailDecision, screen_user_requirement
from repoagent.workspace_utils import get_runs_dir, setup_workspace

load_dotenv("/app/.env", override=True)
load_dotenv(override=True)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="RepoAgent API",
    description="Autonomous requirements-to-PR agentic coding system",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active in-memory registry of flows currently executing in background
_RUNNING_FLOWS: dict[str, RepoAgentFlow] = {}
# Active runs undergoing recovery (mutually exclusive with execution registration)
_RECOVERING_RUNS: set[str] = set()
_LOCK = threading.Lock()


def _execute_run_in_background(flow: RepoAgentFlow) -> None:
    """Execute RepoAgentFlow in a background thread."""
    run_id = flow.state.run_id
    with _LOCK:
        if run_id in _RECOVERING_RUNS:
            sanitized_msg = (
                f"Cannot register active run '{run_id}': run is currently undergoing recovery."
            )
            logger.error(sanitized_msg)
            flow.state.status = RunStatus.NEEDS_HUMAN
            flow.state.finished_at = datetime.now(UTC)
            flow.state.errors.append(sanitized_msg)
            return
        _RUNNING_FLOWS[run_id] = flow
    try:
        flow.kickoff()
        # Post-kickoff verification: Ensure terminal run has finished_at and is persisted
        if flow.state.status != RunStatus.RUNNING and not flow.state.finished_at:
            flow.state.finished_at = datetime.now(UTC)
            flow._save_checkpoint()
    except Exception as exc:
        sanitized_err = redact_text_for_tracing(str(exc))
        logger.error("Background execution for run %s failed: %s", run_id, sanitized_err)
        flow.state.status = RunStatus.NEEDS_HUMAN
        flow.state.finished_at = datetime.now(UTC)
        flow.state.errors.append(f"Unexpected background error: {sanitized_err}")

        # Persist final state and run reports safely
        run_dir = Path(flow.workspace).parent if flow.workspace else get_runs_dir() / run_id
        try:
            saved = flow._save_checkpoint()
            if not saved:
                logger.error(
                    "Failed to save final checkpoint for run %s following background error", run_id
                )
        except Exception as save_exc:
            logger.error(
                "Exception while saving final checkpoint for run %s: %s",
                run_id,
                redact_text_for_tracing(str(save_exc)),
            )

        try:
            from repoagent.pr_agent import write_run_reports

            write_run_reports(run_dir, flow.state)
        except Exception as report_exc:
            logger.error(
                "Exception while writing run reports for run %s: %s",
                run_id,
                redact_text_for_tracing(str(report_exc)),
            )
    finally:
        with _LOCK:
            _RUNNING_FLOWS.pop(run_id, None)


@app.post(
    "/runs",
    response_model=RunCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new RepoAgent run",
)
def create_run(
    payload: RunCreateRequest,
    background_tasks: BackgroundTasks,
) -> RunCreateResponse:
    """Create and start an autonomous RepoAgent run."""
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    runs_dir = get_runs_dir()
    run_dir = runs_dir / run_id
    isolated_workspace = run_dir / "workspace"

    # 1. Screen user requirement before launching planning or coding
    screening = screen_user_requirement(payload.requirement, run_id=run_id)
    if screening.decision == GuardrailDecision.BLOCK:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": f"Requirement blocked by input guardrail: {screening.reason}",
                "category": screening.category.value,
                "detected_patterns": screening.detected_patterns,
            },
        )

    # 2. Setup isolated workspace
    source_repo = Path(payload.repo_url).resolve()
    if not source_repo.exists():
        # Fallback for Docker container environments where repo is mounted under /workspace
        workspace_candidate = Path("/workspace") / payload.repo_url
        if workspace_candidate.exists():
            source_repo = workspace_candidate.resolve()
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Target repository path '{payload.repo_url}' does not exist.",
            )

    setup_workspace(source_repo, isolated_workspace)

    # 3. Initialize RunState
    initial_status = (
        RunStatus.NEEDS_HUMAN if screening.decision == GuardrailDecision.FLAG else RunStatus.RUNNING
    )
    state = RunState(
        run_id=run_id,
        requirement=payload.requirement,
        repo_url=str(isolated_workspace),
        max_retries=payload.max_retries,
        is_replay=payload.is_replay,
        status=initial_status,
    )
    if screening.decision == GuardrailDecision.FLAG:
        state.errors.append(f"User requirement flagged for human review: {screening.reason}")

    # 4. Instantiate Orchestrator Flow
    flow = RepoAgentFlow(
        initial_state=state,
        workspace=isolated_workspace,
        simulate_test_failures=payload.simulate_failures,
        use_live_agents=payload.use_live_agents,
        create_pr=payload.create_pr,
    )
    flow._save_checkpoint()

    # 5. Dispatch execution (if not flagged for immediate human review)
    if screening.decision != GuardrailDecision.FLAG:
        if payload.sync:
            flow.kickoff()
        else:
            background_tasks.add_task(_execute_run_in_background, flow)

    now_iso = datetime.now(UTC).isoformat()
    return RunCreateResponse(
        run_id=run_id,
        status=flow.state.status,
        requirement=payload.requirement,
        repo_url=str(source_repo),
        created_at=now_iso,
        message=(
            "Run flagged for human review"
            if flow.state.status == RunStatus.NEEDS_HUMAN
            else "Run created and queued for execution"
        ),
    )


@app.get(
    "/runs",
    response_model=list[RunSummary],
    summary="List all runs",
)
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
) -> list[RunSummary]:
    """Retrieve summary information for recorded runs."""
    runs_dir = get_runs_dir()
    if not runs_dir.exists():
        return []

    summaries: list[RunSummary] = []
    # Sort runs directories by modification time (most recent first)
    run_paths = sorted(
        [p for p in runs_dir.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    for run_path in run_paths[:limit]:
        run_id = run_path.name
        state_file = run_path / "run_state.json"
        if not state_file.is_file():
            state_file = run_path / "report.json"

        if state_file.is_file():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                pr_info = data.get("pr") or {}
                with _LOCK:
                    is_active = run_id in _RUNNING_FLOWS
                run_status = RunStatus(data.get("status", "running"))
                is_stale = run_status == RunStatus.RUNNING and not is_active
                summaries.append(
                    RunSummary(
                        run_id=run_id,
                        status=run_status,
                        requirement=data.get("requirement", ""),
                        repo_url=data.get("repo_url", ""),
                        started_at=data.get("started_at"),
                        finished_at=data.get("finished_at"),
                        pr_url=pr_info.get("url"),
                        error_count=len(data.get("errors", [])),
                        is_active=is_active,
                        is_stale=is_stale,
                    )
                )
            except Exception as e:
                logger.debug("Error parsing state file for run %s: %s", run_id, e)

    return summaries


@app.get(
    "/runs/{run_id}",
    response_model=RunResponse,
    summary="Get run status and results",
)
def get_run(run_id: str) -> RunResponse:
    """Retrieve structured details, findings, test reports, and PR status for a run."""
    try:
        clean_id = validate_run_id_safe(run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    run_dir = get_runs_dir() / clean_id
    if not run_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{clean_id}' not found.",
        )

    # Prefer active in-memory state if flow is currently running
    with _LOCK:
        active_flow = _RUNNING_FLOWS.get(clean_id)
        if active_flow:
            state = active_flow.state
            trace_url = get_langfuse_tracker().get_trace_url(clean_id)
            duration = None
            if state.started_at:
                end_time = state.finished_at or datetime.now(UTC)
                duration = round((end_time - state.started_at).total_seconds(), 2)
            return RunResponse(
                run_id=state.run_id,
                status=state.status,
                requirement=state.requirement,
                repo_url=state.repo_url,
                branch=state.branch,
                started_at=state.started_at.isoformat() if state.started_at else None,
                finished_at=state.finished_at.isoformat() if state.finished_at else None,
                duration_s=duration,
                retry_count=state.retry_count,
                max_retries=state.max_retries,
                test_reports=state.test_reports,
                review=state.review,
                pr=state.pr,
                errors=state.errors,
                rejected_changes_count=len(state.rejected_changes),
                trace_url=trace_url,
                is_active=True,
                is_stale=False,
            )

    # Read saved checkpoint from disk
    state_file = run_dir / "run_state.json"
    if not state_file.is_file():
        state_file = run_dir / "report.json"

    if not state_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run state for '{clean_id}' not found.",
        )

    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        state = RunState(**data)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse run state: {exc}",
        ) from exc

    duration = None
    if state.started_at and state.finished_at:
        duration = round((state.finished_at - state.started_at).total_seconds(), 2)

    trace_url = get_langfuse_tracker().get_trace_url(clean_id)
    is_stale = state.status == RunStatus.RUNNING

    return RunResponse(
        run_id=state.run_id,
        status=state.status,
        requirement=state.requirement,
        repo_url=state.repo_url,
        branch=state.branch,
        started_at=state.started_at.isoformat() if state.started_at else None,
        finished_at=state.finished_at.isoformat() if state.finished_at else None,
        duration_s=duration,
        retry_count=state.retry_count,
        max_retries=state.max_retries,
        test_reports=state.test_reports,
        review=state.review,
        pr=state.pr,
        errors=state.errors,
        rejected_changes_count=len(state.rejected_changes),
        trace_url=trace_url,
        is_active=False,
        is_stale=is_stale,
    )


@app.post(
    "/runs/{run_id}/recover",
    response_model=RunResponse,
    summary="Explicitly recover an interrupted/stale RUNNING run",
)
def recover_stale_run(run_id: str) -> RunResponse:
    """Explicitly mark an interrupted stale RUNNING run as NEEDS_HUMAN.

    Never automatically resumes execution. Preserves all history, sets finished_at,
    records an audit error, and atomically updates the checkpoint.

    Single-Process Limitation:
    Mutual exclusion and active registry tracking (_LOCK, _RUNNING_FLOWS, _RECOVERING_RUNS)
    are strictly scoped to the current Python process. In a multi-process deployment
    (e.g., multiple uvicorn workers or distributed nodes), cross-process coordination
    via distributed locks or file locking is required.
    """
    clean_id = validate_run_id_safe(run_id)

    with _LOCK:
        if clean_id in _RUNNING_FLOWS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Run '{clean_id}' is currently active in memory and cannot be recovered.",
            )
        if clean_id in _RECOVERING_RUNS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Run '{clean_id}' is currently undergoing recovery.",
            )
        _RECOVERING_RUNS.add(clean_id)

        try:
            run_dir = get_runs_dir() / clean_id
            state_file = run_dir / "run_state.json"
            if not state_file.is_file():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Run state for '{clean_id}' not found.",
                )

            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                state = RunState(**data)
            except HTTPException:
                raise
            except Exception as exc:
                sanitized_exc = redact_text_for_tracing(str(exc))
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to load run state: {sanitized_exc}",
                ) from exc

            if state.status != RunStatus.RUNNING:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Run '{clean_id}' has status '{state.status.value}', "
                        "which is not stale RUNNING."
                    ),
                )

            # Transition safely to NEEDS_HUMAN
            state.status = RunStatus.NEEDS_HUMAN
            if not state.finished_at:
                state.finished_at = datetime.now(UTC)
            state.errors.append(
                "Run execution was interrupted and marked as NEEDS_HUMAN via human recovery."
            )

            # Write atomically
            state_json = state.model_dump_json(indent=2)
            tmp_file = run_dir / f"run_state.tmp.{uuid.uuid4().hex}"
            try:
                tmp_file.write_text(state_json, encoding="utf-8")
                os.replace(tmp_file, state_file)
            except Exception as write_exc:
                if tmp_file.exists():
                    tmp_file.unlink(missing_ok=True)
                sanitized_exc = redact_text_for_tracing(str(write_exc))
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to persist recovered state: {sanitized_exc}",
                ) from write_exc

            from repoagent.pr_agent import write_run_reports

            write_run_reports(run_dir, state)
        finally:
            _RECOVERING_RUNS.discard(clean_id)

    return get_run(clean_id)


@app.get(
    "/runs/{run_id}/events",
    response_model=RunEventsResponse,
    summary="Get recorded audit events for a run",
)
def get_run_events(
    run_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> RunEventsResponse:
    """Retrieve paginated events from runs/<run_id>/events.jsonl."""
    try:
        clean_id = validate_run_id_safe(run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    run_dir = get_runs_dir() / clean_id
    if not run_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{clean_id}' not found.",
        )

    events_file = run_dir / "events.jsonl"
    if not events_file.is_file():
        return RunEventsResponse(
            run_id=clean_id,
            total_events=0,
            offset=offset,
            limit=limit,
            events=[],
        )

    all_events: list[dict[str, Any]] = []
    try:
        for line in events_file.read_text(encoding="utf-8").splitlines():
            line_str = line.strip()
            if line_str:
                raw_event = json.loads(line_str)
                # Ensure additional sanitization of secrets in returned events
                sanitized_event = json.loads(redact_text_for_tracing(json.dumps(raw_event)))
                all_events.append(sanitized_event)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read events for run '{clean_id}': {exc}",
        ) from exc

    total = len(all_events)
    paginated = all_events[offset : offset + limit]

    return RunEventsResponse(
        run_id=clean_id,
        total_events=total,
        offset=offset,
        limit=limit,
        events=paginated,
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Application health check",
)
def health_check() -> HealthResponse:
    """Verify application health and component connectivity without leaking secrets."""
    runs_dir = get_runs_dir()
    runs_writable = False
    try:
        test_file = runs_dir / ".healthcheck"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        runs_writable = True
    except Exception:
        runs_writable = False

    langfuse_active = get_langfuse_tracker().is_enabled

    return HealthResponse(
        status="healthy" if runs_writable else "degraded",
        version="0.1.0",
        runs_dir_writable=runs_writable,
        langfuse_enabled=langfuse_active,
        timestamp=datetime.now(UTC).isoformat(),
    )
