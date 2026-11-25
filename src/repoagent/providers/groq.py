"""Groq API LLM provider adapter with automatic model fallback and tool sanitization.

Supports primary model (default: openai/gpt-oss-20b) with automatic fallback
to secondary model (default: qwen/qwen3.8-27b) on failure, error, or timeout
across both CrewAI and Google ADK.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.integrations.openai._openai_llm import OpenAILlm
from pydantic import PrivateAttr

from crewai import LLM
from repoagent.providers.base import LLMProvider
from repoagent.security.tool_guardrails import sanitize_tool_name

logger = logging.getLogger(__name__)

DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_PRIMARY_MODEL = "openai/gpt-oss-20b"
DEFAULT_FALLBACK_MODEL = "qwen/qwen3.8-27b"
MAX_TRANSIENT_RETRIES = 2


class GroqRateLimitError(RuntimeError):
    """Raised when both Groq primary and fallback models are rate-limited or exhausted."""

    def __init__(self, message: str, status: str = "needs_human") -> None:
        super().__init__(message)
        self.status = status


def is_permanent_error(exc: Exception) -> bool:
    """Return True if the error is a permanent schema/validation error that must NOT be retried."""
    status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status_code == 400:
        return True
    msg = str(exc).lower()
    permanent_keywords = (
        "invalid_request_error",
        "tool_use_failed",
        "schema_error",
        "unrecognized request argument",
        "unknown parameter",
        "extra fields not permitted",
        "unexpected keyword argument",
    )
    return any(k in msg for k in permanent_keywords)


def is_daily_limit_exceeded(exc: Exception) -> bool:
    """Return True if the error indicates a non-recoverable daily quota limit (TPD)."""
    msg = str(exc).lower()
    return "tokens per day" in msg or "tpd" in msg or "rate limit reached for model" in msg and "daily" in msg


def is_rate_limit_or_transient(exc: Exception) -> bool:
    """Return True if the error is transient/rate-limiting (429, 502, 503, 504, timeout)."""
    if is_permanent_error(exc):
        return False
    status_code = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status_code in (429, 502, 503, 504):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    msg = str(exc).lower()
    transient_keywords = (
        "rate limit",
        "rate_limit",
        "tpm",
        "tpd",
        "tokens per minute",
        "tokens per day",
        "too many requests",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "service unavailable",
    )
    return any(k in msg for k in transient_keywords)


def calculate_retry_delay(
    attempt: int,
    exc: Exception | None = None,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
) -> float:
    """Calculate exponential backoff with jitter, respecting Retry-After headers if present."""
    delay: float | None = None
    if exc:
        resp = getattr(exc, "response", None)
        if resp and hasattr(resp, "headers"):
            retry_header = resp.headers.get("retry-after") or resp.headers.get("retry-after-ms")
            if retry_header:
                try:
                    val = float(retry_header)
                    delay = val / 1000.0 if "ms" in str(retry_header).lower() else val
                except ValueError:
                    pass
        if delay is None:
            m = re.search(r"try again in ([0-9.]+)(s|ms)?", str(exc), re.IGNORECASE)
            if m:
                val = float(m.group(1))
                unit = (m.group(2) or "s").lower()
                delay = val / 1000.0 if unit == "ms" else val

    if delay is None:
        delay = min(base_delay * (2**attempt), max_delay)

    # Bounded jitter between 0.1s and 0.5s
    jitter = random.uniform(0.1, 0.5)
    return min(delay + jitter, max_delay)


def prepare_qwen_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Bound parameters and trim oversized messages for Qwen 3.8 27B fallback.

    Prevents HTTP 413 Payload Too Large and OTPM 1000 limits.
    """
    kw = dict(kwargs)
    # Bound max_tokens to 600 so OTPM 1000 is never breached
    kw["max_tokens"] = min(int(kw.get("max_tokens") or 600), 600)

    # Trim oversized messages if present
    messages = kw.get("messages")
    if isinstance(messages, list) and messages:
        trimmed_messages = []
        for msg in messages:
            if isinstance(msg, dict):
                m = dict(msg)
                content = m.get("content")
                if isinstance(content, str) and len(content) > 2500:
                    m["content"] = content[:2000] + "\n... [truncated for fallback context limit]"
                trimmed_messages.append(m)
            else:
                trimmed_messages.append(msg)
        # If total messages are long, preserve system message + last 4 messages
        if len(trimmed_messages) > 6:
            system_msgs = [
                m for m in trimmed_messages if isinstance(m, dict) and m.get("role") == "system"
            ]
            recent_msgs = trimmed_messages[-4:]
            trimmed_messages = system_msgs + [m for m in recent_msgs if m not in system_msgs]
        kw["messages"] = trimmed_messages

    return kw


def sanitize_response_tool_calls(res: Any) -> Any:
    """Sanitize any tool call function names found in a completion or list of tool calls."""
    if res is None:
        return res

    # Single tool call or item
    if hasattr(res, "function") and hasattr(res.function, "name"):
        res.function.name = sanitize_tool_name(res.function.name)
        return res

    # List of tool calls
    if isinstance(res, list):
        for item in res:
            if hasattr(item, "function") and hasattr(item.function, "name"):
                item.function.name = sanitize_tool_name(item.function.name)
        return res

    # ModelResponse or ChatCompletion
    if hasattr(res, "choices"):
        for choice in res.choices:
            msg = getattr(choice, "message", None)
            if msg and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    if hasattr(tc, "function") and hasattr(tc.function, "name"):
                        tc.function.name = sanitize_tool_name(tc.function.name)
            delta = getattr(choice, "delta", None)
            if delta and hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc in delta.tool_calls:
                    if hasattr(tc, "function") and hasattr(tc.function, "name"):
                        tc.function.name = sanitize_tool_name(tc.function.name)

    return res


class FallbackOpenAILlm(OpenAILlm):
    """Google ADK OpenAILlm with automatic fallback on error or timeout."""

    fallback_model: str = DEFAULT_FALLBACK_MODEL
    _fallback_llm: Any = PrivateAttr(default=None)

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        self._fallback_llm = OpenAILlm(
            model=self.fallback_model,
            base_url=self.base_url,
            api_key=self.api_key,
            max_tokens=min(self.max_tokens or 600, 600),
        )

        def _sanitize_groq_kwargs(kw: dict[str, Any]) -> dict[str, Any]:
            if kw.get("tool_choice") is None:
                kw.pop("tool_choice", None)
            if not kw.get("tools"):
                kw.pop("tools", None)
                kw.pop("tool_choice", None)
            return kw

        # Patch primary ADK client to sanitize Groq-incompatible None tool_choice and sanitize output
        orig_primary_create = self._openai_client.chat.completions.create

        async def sanitized_primary_create(*args: Any, **kwargs: Any) -> Any:
            resp = await orig_primary_create(*args, **_sanitize_groq_kwargs(kwargs))
            return sanitize_response_tool_calls(resp)

        self._openai_client.chat.completions.create = sanitized_primary_create

        # Patch fallback ADK client similarly
        orig_fallback_create = self._fallback_llm._openai_client.chat.completions.create

        async def sanitized_fallback_create(*args: Any, **kwargs: Any) -> Any:
            resp = await orig_fallback_create(*args, **_sanitize_groq_kwargs(kwargs))
            return sanitize_response_tool_calls(resp)

        self._fallback_llm._openai_client.chat.completions.create = sanitized_fallback_create

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[Any, None]:
        """Generate content from primary model with bounded retries and fallback."""
        if getattr(self, "_is_exhausted", False):
            logger.info("Primary ADK model '%s' is exhausted (TPD). Directly falling back to '%s'", self.model, self.fallback_model)
            async for response in self._fallback_llm.generate_content_async(llm_request, stream=stream):
                yield sanitize_response_tool_calls(response)
            return

        primary_success = False
        attempt = 0

        while attempt <= MAX_TRANSIENT_RETRIES:
            try:
                chunks = []
                has_error = False
                async for response in super().generate_content_async(llm_request, stream=stream):
                    err_code = getattr(response, "error_code", None)
                    err_msg = getattr(response, "error_message", None)
                    if err_code or err_msg:
                        has_error = True
                        break
                    chunks.append(sanitize_response_tool_calls(response))

                if has_error:
                    break

                for chunk in chunks:
                    yield chunk
                primary_success = True
                return

            except Exception as exc:
                if is_permanent_error(exc):
                    logger.warning(
                        "Primary Groq ADK model '%s' hit permanent error: %s. Not retrying.",
                        self.model,
                        exc,
                    )
                    break

                if is_daily_limit_exceeded(exc):
                    logger.warning(
                        "Primary Groq ADK model '%s' exhausted daily token quota (TPD). Bypassing retries and switching to fallback '%s'.",
                        self.model,
                        self.fallback_model,
                    )
                    self._is_exhausted = True
                    break

                if is_rate_limit_or_transient(exc) and attempt < MAX_TRANSIENT_RETRIES:
                    delay = calculate_retry_delay(attempt, exc)
                    if delay >= 20.0 and attempt > 0:
                        self._is_exhausted = True
                        break
                    logger.warning(
                        "Primary Groq ADK model transient error (%s). Retrying in %.2fs (attempt %d/%d)",
                        exc,
                        delay,
                        attempt + 1,
                        MAX_TRANSIENT_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                else:
                    logger.warning(
                        "Primary Groq ADK model '%s' failed (%s). Proceeding to fallback.",
                        self.model,
                        exc,
                    )
                    break

        if not primary_success:
            logger.info("Falling back to secondary model '%s' in ADK", self.fallback_model)
            try:
                async for response in self._fallback_llm.generate_content_async(
                    llm_request, stream=stream
                ):
                    yield sanitize_response_tool_calls(response)
            except Exception as fb_exc:
                logger.error(
                    "Secondary Groq ADK model '%s' also failed: %s", self.fallback_model, fb_exc
                )
                raise GroqRateLimitError(
                    f"Both primary model '{self.model}' and fallback '{self.fallback_model}' failed or are rate-limited: {fb_exc}"
                ) from fb_exc


class GroqProvider(LLMProvider):
    """Groq API provider for fast inference with automatic fallback."""

    def __init__(
        self,
        model_name: str | None = None,
        fallback_model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int | float | None = None,
    ) -> None:
        self._model_name = (
            model_name
            or os.environ.get("REPOAGENT_MODEL")
            or os.environ.get("GROQ_MODEL")
            or DEFAULT_PRIMARY_MODEL
        )
        self._fallback_model = (
            fallback_model
            or os.environ.get("REPOAGENT_FALLBACK_MODEL")
            or os.environ.get("GROQ_FALLBACK_MODEL")
            or DEFAULT_FALLBACK_MODEL
        )
        self._base_url = base_url or os.environ.get("GROQ_BASE_URL") or DEFAULT_GROQ_BASE_URL
        self._api_key = (
            api_key or os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        )
        try:
            env_timeout = float(os.environ.get("REPOAGENT_LLM_TIMEOUT", "60"))
        except (ValueError, TypeError):
            env_timeout = 60.0
        self._timeout = timeout if timeout is not None else env_timeout

        # Ensure environment variables are populated for downstream clients
        if self._api_key:
            os.environ.setdefault("GROQ_API_KEY", self._api_key)
            if not os.environ.get("OPENAI_API_KEY"):
                os.environ["OPENAI_API_KEY"] = self._api_key

        os.environ.setdefault("OPENAI_BASE_URL", self._base_url)
        os.environ.setdefault("OPENAI_API_BASE", self._base_url)

    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def fallback_model(self) -> str:
        return self._fallback_model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def timeout(self) -> int | float:
        return self._timeout

    def _normalize_crewai_model(self, model: str) -> str:
        """Format model for LiteLLM / CrewAI with OpenAI-compatible routing."""
        if not model.startswith("openai/"):
            return f"openai/{model}"
        return model

    def get_crewai_llm(self) -> LLM:
        """Return a CrewAI LLM configured for Groq with automatic fallback on failure/timeout."""
        primary_model_str = self._normalize_crewai_model(self._model_name)
        fallback_model_str = self._normalize_crewai_model(self._fallback_model)

        primary_llm = LLM(
            model=primary_model_str,
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout,
        )
        primary_llm.model = self._model_name

        fallback_llm = LLM(
            model=fallback_model_str,
            base_url=self._base_url,
            api_key=self._api_key,
            timeout=self._timeout,
        )
        fallback_llm.model = self._fallback_model
        fallback_llm.max_tokens = 600

        orig_call = primary_llm.call
        orig_acall = primary_llm.acall
        primary_name = self._model_name
        fallback_name = self._fallback_model

        def _sanitize_groq_call_kwargs(kw: dict[str, Any]) -> dict[str, Any]:
            if kw.get("tool_choice") is None:
                kw.pop("tool_choice", None)
            if not kw.get("tools"):
                kw.pop("tools", None)
                kw.pop("tool_choice", None)
            return kw

        def wrapped_call(*args: Any, **kwargs: Any) -> Any:
            sanitized_kw = _sanitize_groq_call_kwargs(dict(kwargs))

            # If primary was already flagged as exhausted, proceed directly to fallback
            if getattr(primary_llm, "_is_exhausted", False):
                logger.info(
                    "Primary model '%s' is exhausted (TPD quota). Directly routing to fallback '%s'",
                    primary_name,
                    fallback_name,
                )
                qwen_kw = prepare_qwen_request(sanitized_kw)
                valid_keys = {"messages", "tools", "callbacks", "available_functions", "from_task", "from_agent", "response_model"}
                clean_call_kw = {k: v for k, v in qwen_kw.items() if k in valid_keys}
                try:
                    res = fallback_llm.call(*args, **clean_call_kw)
                    return sanitize_response_tool_calls(res)
                except Exception as fb_exc:
                    raise GroqRateLimitError(
                        f"Both primary model '{primary_name}' and fallback '{fallback_name}' failed: {fb_exc}"
                    ) from fb_exc

            attempt = 0
            last_exc: Exception | None = None

            while attempt <= MAX_TRANSIENT_RETRIES:
                try:
                    res = orig_call(*args, **sanitized_kw)
                    return sanitize_response_tool_calls(res)
                except Exception as exc:
                    last_exc = exc
                    if is_permanent_error(exc):
                        logger.warning(
                            "Primary CrewAI Groq model '%s' hit permanent error: %s. Not retrying.",
                            primary_name,
                            exc,
                        )
                        break

                    # If Daily Limit (TPD) was exceeded or reset is minutes away, do NOT sleep/retry
                    if is_daily_limit_exceeded(exc):
                        logger.warning(
                            "Primary Groq model '%s' exhausted daily token quota (TPD). Bypassing retries and switching to fallback '%s'.",
                            primary_name,
                            fallback_name,
                        )
                        primary_llm._is_exhausted = True
                        break

                    if is_rate_limit_or_transient(exc) and attempt < MAX_TRANSIENT_RETRIES:
                        delay = calculate_retry_delay(attempt, exc)
                        # If provider specifies wait time > 60s, do not stall the agent run
                        if delay >= 20.0 and attempt > 0:
                            primary_llm._is_exhausted = True
                            break
                        logger.warning(
                            "Primary CrewAI Groq model transient error (%s). Retrying in %.2fs (attempt %d/%d)",
                            exc,
                            delay,
                            attempt + 1,
                            MAX_TRANSIENT_RETRIES,
                        )
                        time.sleep(delay)
                        attempt += 1
                    else:
                        break

            # Primary exhausted or failed; attempt fallback to Qwen
            logger.warning(
                "Primary Groq CrewAI model '%s' failed or timed out: %s. Falling back to '%s'",
                primary_name,
                last_exc,
                fallback_name,
            )
            qwen_kw = prepare_qwen_request(sanitized_kw)
            # Filter kwargs to only arguments accepted by CrewAI BaseLLM.call()
            valid_keys = {"messages", "tools", "callbacks", "available_functions", "from_task", "from_agent", "response_model"}
            clean_call_kw = {k: v for k, v in qwen_kw.items() if k in valid_keys}
            try:
                res = fallback_llm.call(*args, **clean_call_kw)
                return sanitize_response_tool_calls(res)
            except Exception as fb_exc:
                logger.error(
                    "Secondary Groq CrewAI model '%s' also failed: %s", fallback_name, fb_exc
                )
                raise GroqRateLimitError(
                    f"Both primary model '{primary_name}' and fallback '{fallback_name}' failed: {fb_exc}"
                ) from fb_exc

        async def wrapped_acall(*args: Any, **kwargs: Any) -> Any:
            sanitized_kw = _sanitize_groq_call_kwargs(dict(kwargs))

            if getattr(primary_llm, "_is_exhausted", False):
                logger.info(
                    "Primary model '%s' is exhausted (TPD quota). Directly routing to fallback '%s'",
                    primary_name,
                    fallback_name,
                )
                qwen_kw = prepare_qwen_request(sanitized_kw)
                valid_keys = {"messages", "tools", "callbacks", "available_functions", "from_task", "from_agent", "response_model"}
                clean_call_kw = {k: v for k, v in qwen_kw.items() if k in valid_keys}
                try:
                    res = await fallback_llm.acall(*args, **clean_call_kw)
                    return sanitize_response_tool_calls(res)
                except Exception as fb_exc:
                    raise GroqRateLimitError(
                        f"Both primary model '{primary_name}' and fallback '{fallback_name}' failed: {fb_exc}"
                    ) from fb_exc

            attempt = 0
            last_exc: Exception | None = None

            while attempt <= MAX_TRANSIENT_RETRIES:
                try:
                    res = await orig_acall(*args, **sanitized_kw)
                    return sanitize_response_tool_calls(res)
                except Exception as exc:
                    last_exc = exc
                    if is_permanent_error(exc):
                        logger.warning(
                            "Primary CrewAI Groq model '%s' hit permanent error: %s. Not retrying.",
                            primary_name,
                            exc,
                        )
                        break

                    if is_daily_limit_exceeded(exc):
                        logger.warning(
                            "Primary Groq model '%s' exhausted daily token quota (TPD). Bypassing retries and switching to fallback '%s'.",
                            primary_name,
                            fallback_name,
                        )
                        primary_llm._is_exhausted = True
                        break

                    if is_rate_limit_or_transient(exc) and attempt < MAX_TRANSIENT_RETRIES:
                        delay = calculate_retry_delay(attempt, exc)
                        if delay >= 20.0 and attempt > 0:
                            primary_llm._is_exhausted = True
                            break
                        logger.warning(
                            "Primary CrewAI Groq model transient error (%s). Retrying in %.2fs (attempt %d/%d)",
                            exc,
                            delay,
                            attempt + 1,
                            MAX_TRANSIENT_RETRIES,
                        )
                        await asyncio.sleep(delay)
                        attempt += 1
                    else:
                        break

            logger.warning(
                "Primary Groq CrewAI model '%s' failed or timed out: %s. Falling back to '%s'",
                primary_name,
                last_exc,
                fallback_name,
            )
            qwen_kw = prepare_qwen_request(sanitized_kw)
            valid_keys = {"messages", "tools", "callbacks", "available_functions", "from_task", "from_agent", "response_model"}
            clean_call_kw = {k: v for k, v in qwen_kw.items() if k in valid_keys}
            try:
                res = await fallback_llm.acall(*args, **clean_call_kw)
                return sanitize_response_tool_calls(res)
            except Exception as fb_exc:
                logger.error(
                    "Secondary Groq CrewAI model '%s' also failed: %s", fallback_name, fb_exc
                )
                raise GroqRateLimitError(
                    f"Both primary model '{primary_name}' and fallback '{fallback_name}' failed: {fb_exc}"
                ) from fb_exc

        primary_llm.call = wrapped_call  # type: ignore[method-assign]
        primary_llm.acall = wrapped_acall  # type: ignore[method-assign]
        primary_llm.fallback_llm = fallback_llm  # type: ignore[attr-defined]

        return primary_llm

    def get_adk_model(self) -> str:
        """Return model identifier recognized by Google ADK."""
        if self._model_name.startswith("openai/"):
            return self._model_name
        return f"openai/{self._model_name}"

    def get_adk_llm(self) -> Any:
        """Return an initialized Google ADK FallbackOpenAILlm configured for Groq."""
        return FallbackOpenAILlm(
            model=self._model_name,
            fallback_model=self._fallback_model,
            base_url=self._base_url,
            api_key=self._api_key or "groq",
        )
