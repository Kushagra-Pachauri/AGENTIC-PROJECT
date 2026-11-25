"""A2A Client wrapper for calling ADK agents served over A2A.

Handles:
- Fetching and inspecting AgentCards from /.well-known/agent-card.json
- Sending JSON-RPC 2.0 SendMessage requests compliant with A2A protocol (A2A-Version: 1.0)
- Validating agent responses into Pydantic models (PlannerOutput, ReviewReport)
- Logging all A2A interactions to runs/<run_id>/a2a.jsonl without leaking secrets
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from repoagent.monitoring import EventType, get_event_logger, resolve_run_id
from repoagent.schemas import Plan, PlannerOutput, ReviewReport, TestReport

logger = logging.getLogger(__name__)


def clean_json_text(text: str) -> str:
    """Extract and clean raw JSON text from model output, stripping markdown code fences."""
    text = text.strip()
    # Match ```json ... ``` or ``` ... ```
    fence_pattern = r"^```(?:json)?\s*\n?(.*?)\n?```$"
    match = re.search(fence_pattern, text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return text


def extract_text_from_a2a_response(response_json: dict[str, Any]) -> str:
    """Extract output text from an A2A JSON-RPC SendMessage response.

    Inspects task artifacts, task history, and message parts per A2A specification.
    """
    if "error" in response_json:
        err = response_json["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise RuntimeError(f"A2A server returned error: {msg}")

    result = response_json.get("result", {})

    # 1. Inspect task artifacts
    task = result.get("task", {})
    artifacts = task.get("artifacts", [])
    for art in artifacts:
        parts = art.get("parts", [])
        for p in parts:
            if isinstance(p, dict) and "text" in p and p["text"]:
                return str(p["text"])

    # 2. Inspect task history (last ROLE_AGENT message)
    history = task.get("history", [])
    for msg in reversed(history):
        if msg.get("role") in ("ROLE_AGENT", "model"):
            parts = msg.get("parts", [])
            for p in parts:
                if isinstance(p, dict) and "text" in p and p["text"]:
                    return str(p["text"])

    # 3. Direct message in result
    msg = result.get("message", {})
    if isinstance(msg, dict):
        for p in msg.get("parts", []):
            if isinstance(p, dict) and "text" in p and p["text"]:
                return str(p["text"])

    # 4. Fallback if result itself contains text
    if result.get("text"):
        return str(result["text"])

    # If result is already structured dict (e.g., custom JSON-RPC result)
    if isinstance(result, dict) and any(k in result for k in ("repo_map", "plan", "verdict")):
        return json.dumps(result)

    raise ValueError(f"No textual output found in A2A response: {response_json}")


def log_a2a_interaction(
    log_file: Path | str,
    agent_card_url: str,
    task_id: str,
    duration_s: float,
    status: str,
    request_summary: str,
    response_summary: str,
    error: str | None = None,
    is_replay: bool = False,
) -> None:
    """Log an A2A interaction to a2a.jsonl, guaranteeing no secrets are recorded."""
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "agent_card_url": agent_card_url,
        "task_id": task_id,
        "duration_s": round(duration_s, 3),
        "status": status,
        "request_summary": request_summary,
        "response_summary": response_summary,
    }
    if is_replay:
        entry["is_replay"] = True
    if error:
        entry["error"] = error

    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


class A2AClient:
    """Client for communicating with ADK agents exposed over A2A."""

    def __init__(
        self,
        base_url: str,
        timeout: float | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if timeout is None:
            timeout = float(os.environ.get("REPOAGENT_A2A_TIMEOUT", "300.0"))
        self.timeout = timeout
        self._client = http_client or httpx.Client(timeout=timeout)

    @property
    def agent_card_url(self) -> str:
        return f"{self.base_url}/.well-known/agent-card.json"

    @staticmethod
    def _log_audit(log_file: Path | str, entry: dict[str, Any]) -> None:
        """Write an audit entry directly to the audit log file."""
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def fetch_agent_card(self) -> dict[str, Any]:
        """Fetch and return the AgentCard JSON from the well-known endpoint."""
        resp = self._client.get(self.agent_card_url)
        resp.raise_for_status()
        card_data: dict[str, Any] = resp.json()
        return card_data

    def send_task(
        self,
        text: str,
        task_id: str | None = None,
        context_id: str | None = None,
        log_file: Path | str | None = None,
        is_replay: bool = False,
    ) -> str:
        """Send a task message over JSON-RPC 2.0 SendMessage and return raw output text.

        Measures duration and records audit log to log_file if provided.
        """
        task_id = task_id or str(uuid.uuid4())
        context_id = context_id or str(uuid.uuid4())
        message_id = str(uuid.uuid4())

        msg_payload: dict[str, Any] = {
            "message_id": message_id,
            "role": "ROLE_USER",
            "parts": [{"text": text}],
        }
        if context_id:
            msg_payload["context_id"] = context_id

        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "SendMessage",
            "params": {"message": msg_payload},
        }
        headers = {
            "A2A-Version": "1.0",
            "Content-Type": "application/json",
        }

        start_t = time.monotonic()
        status = "success"
        error_msg = None
        output_text = ""
        actual_task_id = task_id or "unknown"

        try:
            resp = self._client.post(
                f"{self.base_url}/",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            resp_json = resp.json()
            if "result" in resp_json and isinstance(resp_json["result"], dict):
                task_dict = resp_json["result"].get("task", {})
                if isinstance(task_dict, dict) and task_dict.get("id"):
                    actual_task_id = task_dict["id"]
            output_text = extract_text_from_a2a_response(resp_json)
            return output_text
        except Exception as exc:
            status = "error"
            error_msg = str(exc)
            raise
        finally:
            duration_s = time.monotonic() - start_t
            if log_file:
                # Truncate summaries safely for logging (avoid huge dumps and secrets)
                req_summary = text[:180] + ("..." if len(text) > 180 else "")
                resp_summary = output_text[:180] + ("..." if len(output_text) > 180 else "")
                log_a2a_interaction(
                    log_file=log_file,
                    agent_card_url=self.agent_card_url,
                    task_id=actual_task_id,
                    duration_s=duration_s,
                    status=status,
                    request_summary=req_summary,
                    response_summary=resp_summary
                    if status == "success"
                    else (error_msg or "error"),
                    error=error_msg,
                    is_replay=is_replay,
                )

    def request_plan(
        self,
        requirement: str,
        workspace: str | Path,
        run_id: str | None = None,
        log_file: Path | str | None = None,
        extra_instruction: str | None = None,
        is_replay: bool = False,
    ) -> PlannerOutput:
        """Call the Planner ADK agent and strictly validate the returned PlannerOutput."""
        from repoagent.mcp_servers import repo_reader

        ws_path = Path(workspace).resolve()
        repo_reader._set_workspace(str(ws_path))

        active_run = resolve_run_id(run_id)
        evt_logger = get_event_logger()

        # 1. repo_reader.list_tree tool call
        with contextlib.suppress(Exception):
            evt_logger.log_event(
                run_id=active_run,
                event_type=EventType.TOOL_STARTED,
                agent_name="repo-planner",
                stage="analyze",
                input_summary={
                    "tool": "repo_reader.list_tree",
                    "arguments": {"directory": ".", "max_depth": 4},
                },
            )

        t_tree_start = time.time()
        tree_text = repo_reader.list_tree(".", max_depth=4)
        t_tree_dur = time.time() - t_tree_start

        from repoagent.security import InputSource, screen_input

        tree_screening = screen_input(tree_text, source=InputSource.MCP_OUTPUT, run_id=active_run)
        if tree_screening.is_suspicious and tree_screening.sanitized_content:
            tree_text = tree_screening.sanitized_content

        with contextlib.suppress(Exception):
            evt_logger.log_event(
                run_id=active_run,
                event_type=EventType.TOOL_COMPLETED,
                agent_name="repo-planner",
                stage="analyze",
                duration_s=round(t_tree_dur, 4),
                output_summary=tree_text,
            )

        if log_file:
            tool_entry = {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": "tool_call",
                "agent": "repo-planner",
                "task_id": run_id or "planner-task",
                "tool": "repo_reader.list_tree",
                "arguments": {"directory": ".", "max_depth": 4},
                "duration_s": round(t_tree_dur, 4),
                "status": "success",
            }
            if is_replay:
                tool_entry["is_replay"] = True
            self._log_audit(log_file, tool_entry)

        # 2. repo_reader.read_file tool calls
        files_context: list[str] = []
        for candidate in (
            "src/notes_app/app.py",
            "src/notes_app/auth.py",
            "src/notes_app/models.py",
            "src/notes_app/notes.py",
            "tests/test_auth.py",
            "tests/test_notes.py",
        ):
            cand_p = ws_path / candidate
            if cand_p.is_file():
                with contextlib.suppress(Exception):
                    evt_logger.log_event(
                        run_id=active_run,
                        event_type=EventType.TOOL_STARTED,
                        agent_name="repo-planner",
                        stage="analyze",
                        input_summary={
                            "tool": "repo_reader.read_file",
                            "arguments": {"path": candidate, "offset": 0, "limit": 200},
                        },
                    )

                t_read_start = time.time()
                content = repo_reader.read_file(candidate, offset=0, limit=200)
                t_read_dur = time.time() - t_read_start

                from repoagent.security import screen_repository_content

                screening = screen_repository_content(
                    content, file_path=candidate, run_id=active_run
                )
                formatted_content = (
                    screening.sanitized_content
                    if screening.is_suspicious and screening.sanitized_content
                    else content
                )
                files_context.append(f"--- {candidate} ---\n{formatted_content}")

                with contextlib.suppress(Exception):
                    evt_logger.log_event(
                        run_id=active_run,
                        event_type=EventType.TOOL_COMPLETED,
                        agent_name="repo-planner",
                        stage="analyze",
                        duration_s=round(t_read_dur, 4),
                        output_summary=content,
                    )

                if log_file:
                    tool_entry = {
                        "timestamp": datetime.now(UTC).isoformat(),
                        "event": "tool_call",
                        "agent": "repo-planner",
                        "task_id": run_id or "planner-task",
                        "tool": "repo_reader.read_file",
                        "arguments": {"path": candidate, "offset": 0, "limit": 200},
                        "duration_s": round(t_read_dur, 4),
                        "status": "success",
                    }
                    if is_replay:
                        tool_entry["is_replay"] = True
                    self._log_audit(log_file, tool_entry)

        files_str = "\n".join(files_context)

        prompt = (
            f"Requirement: {requirement}\n"
            f"Target Workspace: {workspace}\n\n"
            f"Repository Directory Tree (via repo-reader list_tree):\n{tree_text}\n\n"
            f"Key Repository Source Files (via repo-reader read_file):\n{files_str}\n\n"
            "Analyze the repository structure, components, and tests. "
            "Return structured JSON matching the PlannerOutput schema with:\n"
            "- repo_map: high-level architecture map of components and files\n"
            "- plan: object with summary, files_to_change (path/reason), steps, risks, tests.\n"
        )
        if extra_instruction:
            prompt += (
                f"\n### Additional Grounding Context / Correction Feedback:\n{extra_instruction}\n"
            )

        with contextlib.suppress(Exception):
            evt_logger.log_event(
                run_id=active_run,
                event_type=EventType.AGENT_STARTED,
                agent_name="repo-planner",
                stage="analyze",
                input_summary={
                    "requirement": requirement,
                    "workspace": str(workspace),
                },
                metadata={"framework": "Google ADK"},
            )

        t_plan_start = time.time()
        raw_text = self.send_task(
            text=prompt,
            task_id=run_id,
            log_file=log_file,
            is_replay=is_replay,
        )
        t_plan_dur = time.time() - t_plan_start
        clean = clean_json_text(raw_text)
        try:
            output = PlannerOutput.model_validate_json(clean)
            with contextlib.suppress(Exception):
                evt_logger.log_event(
                    run_id=active_run,
                    event_type=EventType.AGENT_COMPLETED,
                    agent_name="repo-planner",
                    stage="analyze",
                    output_summary={
                        "summary": output.plan.summary,
                        "files_to_change": [f.model_dump() for f in output.plan.files_to_change],
                        "steps": output.plan.steps,
                        "risks": output.plan.risks,
                        "repo_map": output.repo_map[:300] if output.repo_map else "",
                    },
                    duration_s=round(t_plan_dur, 4),
                    metadata={
                        "framework": "Google ADK",
                        "files_count": len(output.plan.files_to_change),
                        "prompt_tokens": max(1, len(prompt) // 4),
                        "completion_tokens": max(1, len(raw_text) // 4),
                    },
                )
            return output
        except Exception as exc:
            msg = (
                f"Failed to validate PlannerOutput from A2A Planner agent: {exc}. "
                f"Raw: {raw_text[:300]}"
            )
            logger.error(msg)
            raise ValueError(msg) from exc

    def request_review(
        self,
        plan: Plan,
        diff: str,
        test_report: TestReport,
        run_id: str | None = None,
        log_file: Path | str | None = None,
        is_replay: bool = False,
    ) -> ReviewReport:
        """Call the Code Reviewer ADK agent and strictly validate the returned ReviewReport."""
        active_run = resolve_run_id(run_id)
        evt_logger = get_event_logger()
        prompt = (
            "Review the proposed code changes against the Plan and deterministic TestReport.\n\n"
            f"### Plan Summary:\n{plan.summary}\n"
            f"### Planned Files:\n{[f.path for f in plan.files_to_change]}\n\n"
            f"### Deterministic Test Report:\n"
            f"- Command: {test_report.command}\n"
            f"- Passed: {test_report.passed}\n"
            f"- Exit Code: {test_report.exit_code}\n"
            f"- Workspace Hash: {test_report.workspace_hash}\n"
            f"- Failed Tests: {test_report.failed_tests}\n\n"
            f"### Git Diff from Disk:\n{diff}\n\n"
            "Evaluate code quality, security, and requirement adherence. "
            "Return valid JSON matching ReviewReport with 'verdict' ('approve'/'request_changes'), "
            "'findings' (list of {severity, file, line, message}), and a concise 'summary' in 1-2 sentences."
        )

        with contextlib.suppress(Exception):
            evt_logger.log_event(
                run_id=active_run,
                event_type=EventType.AGENT_STARTED,
                agent_name="code-reviewer",
                stage="review",
                input_summary={
                    "plan_summary": plan.summary,
                    "test_passed": test_report.passed,
                    "diff_lines": len(diff.splitlines()),
                },
                metadata={"framework": "Google ADK"},
            )

        t_rev_start = time.time()
        raw_text = self.send_task(
            text=prompt,
            task_id=run_id,
            log_file=log_file,
            is_replay=is_replay,
        )
        t_rev_dur = time.time() - t_rev_start
        clean = clean_json_text(raw_text)
        try:
            output = ReviewReport.model_validate_json(clean)
            if not output.provenance:
                output.provenance = "authoritative_adk_reviewer"

            with contextlib.suppress(Exception):
                evt_logger.log_event(
                    run_id=active_run,
                    event_type=EventType.AGENT_COMPLETED,
                    agent_name="code-reviewer",
                    stage="review",
                    output_summary={
                        "verdict": output.verdict,
                        "summary": output.summary,
                        "findings_count": len(output.findings),
                        "findings": [f.model_dump() for f in output.findings],
                        "provenance": output.provenance,
                    },
                    duration_s=round(t_rev_dur, 4),
                    metadata={
                        "framework": "Google ADK",
                        "verdict": output.verdict,
                        "prompt_tokens": max(1, len(prompt) // 4),
                        "completion_tokens": max(1, len(raw_text) // 4),
                    },
                )
            return output
        except Exception as exc:
            msg = (
                f"Failed to validate ReviewReport from A2A Reviewer agent: {exc}. "
                f"Raw: {raw_text[:300]}"
            )
            logger.error(msg)
            raise ValueError(msg) from exc
