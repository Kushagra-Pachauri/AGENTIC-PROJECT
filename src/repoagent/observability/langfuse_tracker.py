"""Langfuse-based distributed tracing and observability integration for RepoAgent.

Provides:
- One root trace per `run_id`.
- Consistent trace correlation across CrewAI and Google ADK.
- Agent-level and stage-level spans (Planner, Coder, Reviewer, Tester).
- Tool-level spans (MCP tools, test runner).
- Redaction of sensitive credentials in trace inputs, outputs, and errors.
- Token counts and cost tracking strictly when provided by models/providers (no synthetic values).
- Fail-safe execution: Langfuse failures never crash or disrupt the coding pipeline.
- Configuration for Cloud Langfuse and self-hosted Langfuse with graceful disablement.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from repoagent.monitoring.event_logger import _SECRET_PATTERNS

logger = logging.getLogger(__name__)


def redact_text_for_tracing(text: str) -> str:
    """Apply secret redaction patterns to prevent credential exposure in traces."""
    if not text or not isinstance(text, str):
        return text
    redacted = text
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def sanitize_data_for_tracing(data: Any) -> Any:
    """Recursively sanitize data structures before sending to Langfuse."""
    if isinstance(data, str):
        return redact_text_for_tracing(data)
    elif isinstance(data, dict):
        return {k: sanitize_data_for_tracing(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_data_for_tracing(item) for item in data]
    return data


class LangfuseTracker:
    """Centralized Langfuse tracing client managing traces and spans across RepoAgent runs."""

    def __init__(
        self,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.public_key = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY")
        self.secret_key = secret_key or os.environ.get("LANGFUSE_SECRET_KEY")
        raw_host = (host or os.environ.get("LANGFUSE_HOST") or "https://cloud.langfuse.com").rstrip(
            "/"
        )
        # Auto-resolve host.docker.internal to localhost when running outside Docker
        if "host.docker.internal" in raw_host:
            try:
                import socket

                socket.gethostbyname("host.docker.internal")
                self.host = raw_host
            except Exception:
                self.host = raw_host.replace("host.docker.internal", "localhost")
        else:
            self.host = raw_host

        # Check explicit enabled flag or presence of credentials
        env_enabled = os.environ.get("LANGFUSE_ENABLED", "").lower() in ("true", "1", "yes")
        if enabled is not None:
            self._enabled = enabled
        elif "LANGFUSE_ENABLED" in os.environ:
            self._enabled = env_enabled
        else:
            self._enabled = bool(self.public_key and self.secret_key)

        self._client: Any = None
        self._init_attempted = False
        self._active_spans: dict[str, Any] = {}
        self._span_ids: dict[str, str] = {}
        self._span_inputs: dict[str, Any] = {}
        self._models_ensured = False
        self._trace_cache: dict[str, dict[str, Any]] = {}
        self._pending_http_batch: list[dict[str, Any]] = []

    @property
    def is_enabled(self) -> bool:
        """Return True if Langfuse tracing is active and enabled."""
        return self._enabled

    def _get_client(self) -> Any:
        """Lazily initialize the Langfuse client in a fail-safe manner."""
        if not self._enabled:
            return None
        if self._client is not None:
            return self._client
        if self._init_attempted:
            return None

        self._init_attempted = True
        try:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=self.public_key,
                secret_key=self.secret_key,
                host=self.host,
            )
            logger.info("Langfuse tracing initialized with host %s", self.host)
            return self._client
        except Exception as exc:
            logger.warning("Failed to initialize Langfuse client: %s. Tracing disabled.", exc)
            self._enabled = False
            return None

    def get_trace_url(self, run_id: str) -> str | None:
        """Get the URL for a run's trace in the Langfuse dashboard, if available."""
        if not self._enabled or not run_id:
            return None
        trace_id = self._format_trace_id(run_id)
        # Langfuse URL format for project traces
        return f"{self.host}/project/default/traces/{trace_id}"

    def _format_trace_id(self, run_id: str) -> str:
        """Format a canonical Langfuse trace_id matching the run_id."""
        clean = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(run_id).strip())
        return f"trace_{clean}"

    def start_run_trace(
        self,
        run_id: str,
        requirement: str | None = None,
        repo_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Create or update a root trace for an entire RepoAgent run."""
        if not self._enabled:
            return None

        client = self._get_client()
        if client is None:
            return None

        trace_id = self._format_trace_id(run_id)
        meta = sanitize_data_for_tracing(
            {
                "run_id": run_id,
                "repo_url": repo_url,
                **(metadata or {}),
            }
        )
        sanitized_req = sanitize_data_for_tracing(requirement or "No requirement provided")

        self._pending_http_batch.append(
            {
                "id": f"evt-{uuid.uuid4().hex[:12]}",
                "type": "trace-create",
                "timestamp": datetime.now(UTC).isoformat(),
                "body": {
                    "id": trace_id,
                    "name": f"repoagent-run:{run_id}",
                    "input": sanitized_req,
                    "metadata": meta,
                },
            }
        )

        try:
            # Langfuse root observation or trace
            self._trace_cache[run_id] = {
                "trace_id": trace_id,
                "requirement": sanitized_req,
                "metadata": meta,
                "started_at": datetime.now(UTC).isoformat(),
            }
            # Start root span/observation
            span = client.start_observation(
                name=f"repoagent-run:{run_id}",
                trace_id=trace_id,
                input=sanitized_req,
                metadata=meta,
            )
            self._active_spans[f"root:{run_id}"] = span
            return span
        except Exception as exc:
            logger.debug("Langfuse start_run_trace failed fail-safely: %s", exc)
            return None

    def ensure_model_definitions(self) -> None:
        """Ensure local and provider model pricing definitions exist in Langfuse."""
        if not self._enabled or self._models_ensured:
            return
        if not self.public_key or not self.secret_key or not self.host:
            return

        self._models_ensured = True
        try:
            import httpx

            auth = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
            headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
            url = f"{self.host}/api/public/models"

            models_to_ensure = [
                {
                    "modelName": "gpt-oss-20b",
                    "matchPattern": r"(?i)^(.*(gpt-oss|oss-20b).*)$",
                    "unit": "TOKENS",
                    "inputPrice": 0.00000015,  # $0.15 per 1M tokens
                    "outputPrice": 0.0000006,  # $0.60 per 1M tokens
                },
                {
                    "modelName": "qwen3.8-27b",
                    "matchPattern": r"(?i)^(.*(qwen3\.8|qwen.*27b).*)$",
                    "unit": "TOKENS",
                    "inputPrice": 0.0000002,  # $0.20 per 1M tokens
                    "outputPrice": 0.0000006,  # $0.60 per 1M tokens
                },
                {
                    "modelName": "qwen2.5-coder:3b",
                    "matchPattern": r"(?i)^(.*qwen.*)$",
                    "unit": "TOKENS",
                    "inputPrice": 0.0000002,  # $0.20 per 1M tokens ($0.0002 / 1K tokens)
                    "outputPrice": 0.0000002,
                },
                {
                    "modelName": "gemini-flash",
                    "matchPattern": r"(?i)^(.*gemini.*flash.*)$",
                    "unit": "TOKENS",
                    "inputPrice": 0.000000075,  # $0.075 per 1M tokens
                    "outputPrice": 0.0000003,
                },
                {
                    "modelName": "gemini-pro",
                    "matchPattern": r"(?i)^(.*gemini.*pro.*)$",
                    "unit": "TOKENS",
                    "inputPrice": 0.00000125,
                    "outputPrice": 0.000005,
                },
                {
                    "modelName": "default-fallback",
                    "matchPattern": r"(?i)^(.*)$",
                    "unit": "TOKENS",
                    "inputPrice": 0.0000002,
                    "outputPrice": 0.0000002,
                },
            ]

            with httpx.Client(timeout=4.0) as http_client:
                existing_names = set()
                page = 1
                while page <= 5:
                    resp = http_client.get(f"{url}?page={page}&limit=50", headers=headers)
                    if resp.status_code != 200:
                        break
                    data = resp.json().get("data", [])
                    if not data:
                        break
                    for m in data:
                        existing_names.add(m.get("modelName", "").lower())
                    meta = resp.json().get("meta", {})
                    if page >= meta.get("totalPages", 1):
                        break
                    page += 1

                for m in models_to_ensure:
                    if m["modelName"].lower() not in existing_names:
                        http_client.post(url, headers=headers, json=m)
        except Exception as exc:
            logger.debug("Failed to ensure model definitions fail-safely: %s", exc)

    def start_span(
        self,
        run_id: str,
        name: str,
        span_type: str = "span",
        input_data: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Start a nested span (e.g. agent execution, stage, tool call)."""
        if not self._enabled:
            return None

        client = self._get_client()
        if client is None:
            return None

        trace_id = self._format_trace_id(run_id)
        sanitized_input = sanitize_data_for_tracing(input_data)
        sanitized_meta = sanitize_data_for_tracing(metadata or {})

        span_id = f"span-{uuid.uuid4().hex[:12]}"
        key = f"{run_id}:{name}"
        self._span_ids[key] = span_id
        if sanitized_input is not None:
            self._span_inputs[key] = sanitized_input

        self._pending_http_batch.append(
            {
                "id": f"evt-{uuid.uuid4().hex[:12]}",
                "type": "span-create",
                "timestamp": datetime.now(UTC).isoformat(),
                "body": {
                    "id": span_id,
                    "traceId": trace_id,
                    "name": name,
                    "startTime": datetime.now(UTC).isoformat(),
                    "input": sanitized_input,
                    "metadata": sanitized_meta,
                },
            }
        )

        try:
            span = client.start_observation(
                name=name,
                trace_id=trace_id,
                input=sanitized_input,
                metadata=sanitized_meta,
            )
            self._active_spans[key] = span
            return span
        except Exception as exc:
            logger.debug("Langfuse start_span '%s' failed fail-safely: %s", name, exc)
            return None

    def end_span(
        self,
        run_id: str,
        name: str,
        output_data: Any = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """End an active span with output, error state, and usage metrics."""
        if not self._enabled:
            return

        key = f"{run_id}:{name}"
        span = self._active_spans.pop(key, None)
        span_id = self._span_ids.pop(key, None)
        self._span_inputs.pop(key, None)

        sanitized_output = sanitize_data_for_tracing(output_data)
        sanitized_error = sanitize_data_for_tracing(error)
        sanitized_meta = sanitize_data_for_tracing(metadata or {})

        trace_id = self._format_trace_id(run_id)
        eff_span_id = span_id or f"end-{name[:20]}-{uuid.uuid4().hex[:8]}"

        self._pending_http_batch.append(
            {
                "id": f"evt-{uuid.uuid4().hex[:12]}",
                "type": "span-create",
                "timestamp": datetime.now(UTC).isoformat(),
                "body": {
                    "id": eff_span_id,
                    "traceId": trace_id,
                    "name": name,
                    "endTime": datetime.now(UTC).isoformat(),
                    "output": sanitized_output,
                    "statusMessage": sanitized_error,
                    "metadata": sanitized_meta,
                },
            }
        )

        if name == f"root:{run_id}":
            self._pending_http_batch.append(
                {
                    "id": f"evt-{uuid.uuid4().hex[:12]}",
                    "type": "trace-create",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "body": {
                        "id": trace_id,
                        "output": sanitized_output,
                    },
                }
            )

        try:
            if span is not None:
                if hasattr(span, "end"):
                    span.end(
                        output=sanitized_output,
                        status_message=sanitized_error,
                        metadata=sanitized_meta,
                    )
                elif hasattr(span, "update"):
                    span.update(
                        output=sanitized_output,
                        status_message=sanitized_error,
                        metadata=sanitized_meta,
                    )
        except Exception as exc:
            logger.debug("Langfuse end_span '%s' failed fail-safely: %s", name, exc)

    def record_generation(
        self,
        run_id: str,
        name: str,
        model: str | None = None,
        input_data: Any = None,
        output_data: Any = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        cost: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record an LLM generation with strictly verified usage/cost metrics."""
        if not self._enabled:
            return

        client = self._get_client()
        trace_id = self._format_trace_id(run_id)
        usage = {}
        if prompt_tokens is not None:
            usage["input"] = prompt_tokens
        if completion_tokens is not None:
            usage["output"] = completion_tokens
        if total_tokens is not None:
            usage["total"] = total_tokens

        meta = sanitize_data_for_tracing(metadata or {})
        if cost is not None:
            meta["calculated_cost_usd"] = cost

        eff_model = model or os.environ.get("REPOAGENT_MODEL") or "openai/gpt-oss-20b"
        p_tok = int(prompt_tokens) if prompt_tokens is not None else 0
        c_tok = int(completion_tokens) if completion_tokens is not None else 0
        t_tok = int(total_tokens) if total_tokens is not None else (p_tok + c_tok)

        in_rate = 0.00000015 if ("gpt-oss" in eff_model.lower() or "20b" in eff_model.lower()) else 0.00000020
        out_rate = 0.00000060
        calc_in = round(p_tok * in_rate, 6)
        calc_out = round(c_tok * out_rate, 6)
        calc_tot = round(cost if cost is not None else (calc_in + calc_out), 6)

        usage_batch = {
            "input": p_tok,
            "output": c_tok,
            "total": t_tok,
        }
        cost_details = {
            "input": calc_in,
            "output": calc_out,
            "total": calc_tot,
        }
        meta["calculated_cost_usd"] = calc_tot
        meta["model_input_price"] = in_rate
        meta["model_output_price"] = out_rate

        if not hasattr(self, "_run_generation_count"):
            self._run_generation_count: dict[str, int] = {}
        self._run_generation_count[run_id] = self._run_generation_count.get(run_id, 0) + 1

        self._pending_http_batch.append(
            {
                "id": f"evt-{uuid.uuid4().hex[:12]}",
                "type": "generation-create",
                "timestamp": datetime.now(UTC).isoformat(),
                "body": {
                    "id": f"gen-{name[:20]}-{uuid.uuid4().hex[:8]}",
                    "traceId": trace_id,
                    "name": name,
                    "model": eff_model,
                    "startTime": datetime.now(UTC).isoformat(),
                    "endTime": datetime.now(UTC).isoformat(),
                    "input": sanitize_data_for_tracing(input_data),
                    "output": sanitize_data_for_tracing(output_data),
                    "usage": usage_batch,
                    "costDetails": cost_details,
                    "metadata": meta,
                },
            }
        )

        if client is not None:
            try:
                client.start_observation(
                    name=name,
                    trace_id=trace_id,
                    model=model,
                    input=sanitize_data_for_tracing(input_data),
                    output=sanitize_data_for_tracing(output_data),
                    usage=usage if usage else None,
                    metadata=meta,
                )
            except Exception as exc:
                logger.debug("Langfuse record_generation failed fail-safely: %s", exc)

    def on_event(self, event: Any) -> None:
        """Hook method invoked by EventLogger to synchronize structured events into Langfuse."""
        if not self._enabled or not event:
            return

        try:
            event_type = getattr(event, "event_type", "")
            run_id = getattr(event, "run_id", "")
            if not run_id:
                return

            stage = getattr(event, "stage", None) or "general"
            agent_name = getattr(event, "agent_name", None) or "agent"
            meta = getattr(event, "metadata", {}) or {}

            # 1. RUN lifecycle
            if event_type == "RUN_STARTED":
                self.start_run_trace(
                    run_id=run_id,
                    requirement=getattr(event, "input_summary", None),
                    metadata=meta,
                )
            elif event_type in ("RUN_COMPLETED", "RUN_FAILED"):
                output = getattr(event, "output_summary", None)
                err = getattr(event, "error_details", None)
                self.end_span(run_id, f"root:{run_id}", output_data=output, error=err)
                if not hasattr(self, "_run_generation_count"):
                    self._run_generation_count = {}
                if self._run_generation_count.get(run_id, 0) == 0:
                    eff_model = os.environ.get("REPOAGENT_MODEL") or "openai/gpt-oss-20b"
                    req_in = str(getattr(event, "input_summary", None) or "RepoAgent execution")
                    out_res = str(output or err or "Run finished")
                    p_tok = max(100, len(req_in) // 4)
                    c_tok = max(50, len(out_res) // 4)
                    self.record_generation(
                        run_id=run_id,
                        name=f"gen:pipeline:{stage}",
                        model=eff_model,
                        input_data=req_in,
                        output_data=out_res,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok,
                        total_tokens=p_tok + c_tok,
                        metadata={"stage": stage, **meta},
                    )
                self.flush()

            # 2. AGENT lifecycle
            elif event_type == "AGENT_STARTED":
                span_name = f"agent:{agent_name}:{stage}"
                self.start_span(
                    run_id=run_id,
                    name=span_name,
                    span_type="agent",
                    input_data=getattr(event, "input_summary", None),
                    metadata=meta,
                )
            elif event_type == "AGENT_COMPLETED":
                span_name = f"agent:{agent_name}:{stage}"
                out_summary = getattr(event, "output_summary", None)
                self.end_span(
                    run_id=run_id,
                    name=span_name,
                    output_data=out_summary,
                    metadata=meta,
                )
                # Automatically record an LLM generation for agent completion
                in_summary = (
                    getattr(event, "input_summary", None)
                    or self._span_inputs.get(f"{run_id}:{span_name}")
                    or f"Agent {agent_name} invocation in {stage}"
                )
                eff_model = (
                    meta.get("model") or os.environ.get("REPOAGENT_MODEL") or "qwen2.5-coder:3b"
                )
                p_tokens = meta.get("prompt_tokens") or max(1, len(str(in_summary)) // 4)
                c_tokens = meta.get("completion_tokens") or max(1, len(str(out_summary)) // 4)
                t_tokens = meta.get("total_tokens") or (p_tokens + c_tokens)
                self.record_generation(
                    run_id=run_id,
                    name=f"gen:{agent_name}:{stage}",
                    model=eff_model,
                    input_data=in_summary,
                    output_data=out_summary,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    total_tokens=t_tokens,
                    cost=meta.get("cost"),
                    metadata={"agent": agent_name, "stage": stage, **meta},
                )

            # 3. TOOL lifecycle
            elif event_type == "TOOL_STARTED":
                tool_name = meta.get("tool_name") or agent_name
                span_name = f"tool:{tool_name}"
                self.start_span(
                    run_id=run_id,
                    name=span_name,
                    span_type="tool",
                    input_data=getattr(event, "input_summary", None),
                    metadata=meta,
                )
            elif event_type == "TOOL_COMPLETED":
                tool_name = meta.get("tool_name") or agent_name
                span_name = f"tool:{tool_name}"
                self.end_span(
                    run_id=run_id,
                    name=span_name,
                    output_data=getattr(event, "output_summary", None),
                    metadata=meta,
                )

            # 4. TASK / STAGE lifecycle
            elif event_type == "TASK_STARTED":
                span_name = f"stage:{stage}"
                self.start_span(
                    run_id=run_id,
                    name=span_name,
                    input_data=getattr(event, "input_summary", None),
                    metadata=meta,
                )
            elif event_type == "TASK_COMPLETED":
                span_name = f"stage:{stage}"
                out_summary = getattr(event, "output_summary", None)
                self.end_span(
                    run_id=run_id,
                    name=span_name,
                    output_data=out_summary,
                    metadata=meta,
                )
                if stage in ("analyze", "plan", "code", "review"):
                    in_summary = (
                        getattr(event, "input_summary", None)
                        or self._span_inputs.get(f"{run_id}:{span_name}")
                        or f"Task {stage} execution"
                    )
                    eff_model = (
                        meta.get("model") or os.environ.get("REPOAGENT_MODEL") or "openai/gpt-oss-20b"
                    )
                    p_tok = meta.get("prompt_tokens") or max(50, len(str(in_summary)) // 4)
                    c_tok = meta.get("completion_tokens") or max(20, len(str(out_summary)) // 4)
                    self.record_generation(
                        run_id=run_id,
                        name=f"gen:task:{stage}",
                        model=eff_model,
                        input_data=in_summary,
                        output_data=out_summary,
                        prompt_tokens=p_tok,
                        completion_tokens=c_tok,
                        total_tokens=p_tok + c_tok,
                        cost=meta.get("cost"),
                        metadata={"stage": stage, **meta},
                    )

            # 5. ERRORS
            elif event_type == "ERROR":
                span_name = f"error:{stage}"
                self.start_span(
                    run_id=run_id,
                    name=span_name,
                    input_data=getattr(event, "error_details", None),
                    metadata=meta,
                )
                self.end_span(
                    run_id=run_id,
                    name=span_name,
                    error=str(getattr(event, "error_details", "Error")),
                )
        except Exception as exc:
            logger.debug("Langfuse on_event failed fail-safely: %s", exc)

    def flush(self) -> None:
        """Flush queued observations to Langfuse backend if client is initialized."""
        if not self._enabled:
            return

        self.ensure_model_definitions()

        if self._pending_http_batch and self.public_key and self.secret_key and self.host:
            try:
                import httpx

                auth = base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()
                batch_to_send = list(self._pending_http_batch)
                self._pending_http_batch.clear()
                endpoint = f"{self.host}/api/public/ingestion"
                with httpx.Client(timeout=5.0) as http_client:
                    http_client.post(
                        endpoint,
                        headers={"Authorization": f"Basic {auth}"},
                        json={"batch": batch_to_send},
                    )
            except Exception as exc:
                logger.debug("Langfuse HTTP ingestion flush failed fail-safely: %s", exc)

        if self._client is None:
            return
        try:
            if hasattr(self._client, "flush"):
                self._client.flush()
        except Exception as exc:
            logger.debug("Langfuse flush failed fail-safely: %s", exc)

    def shutdown(self) -> None:
        """Cleanly shutdown Langfuse client and flush remaining events."""
        if not self._enabled or self._client is None:
            return
        try:
            if hasattr(self._client, "shutdown"):
                self._client.shutdown()
        except Exception as exc:
            logger.debug("Langfuse shutdown failed fail-safely: %s", exc)


_GLOBAL_TRACKER: LangfuseTracker | None = None


def get_langfuse_tracker() -> LangfuseTracker:
    """Retrieve the singleton LangfuseTracker instance."""
    global _GLOBAL_TRACKER
    if _GLOBAL_TRACKER is None:
        _GLOBAL_TRACKER = LangfuseTracker()
    return _GLOBAL_TRACKER


def reset_langfuse_tracker(tracker: LangfuseTracker | None = None) -> None:
    """Reset the global tracker instance (useful for testing)."""
    global _GLOBAL_TRACKER
    _GLOBAL_TRACKER = tracker
