"""Capped exponential backoff with jitter for LLM provider API calls.

Handles transient 429 (Resource Exhausted / Rate Limit) and 503 (Unavailable / High Demand)
responses up to a configurable maximum attempt cap (default: 4 attempts).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")
logger = logging.getLogger(__name__)


class RateLimitOrDemandExceededError(RuntimeError):
    """Raised when retry cap is reached for 429 or 503 errors."""


def is_transient_error(error: Exception) -> tuple[bool, str]:
    """Check if an exception represents a 429 or 503 rate/demand error."""
    msg = str(error)
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
        return True, "429 Rate Limit / Quota Exceeded"
    if "503" in msg or "UNAVAILABLE" in msg or "high demand" in msg.lower():
        return True, "503 Service Unavailable / High Demand"
    return False, ""


def retry_with_backoff(
    operation: Callable[[], _T],
    *,
    model_name: str,
    max_attempts: int = 4,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> _T:
    """Execute *operation* with capped exponential backoff and jitter on 429/503 errors.

    Args:
        operation: Zero-argument callable to invoke.
        model_name: Model name for clear failure attribution.
        max_attempts: Maximum number of execution attempts (default: 4).
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.
        jitter: Whether to add random jitter to the delay.
        sleep_fn: Sleep function (injectable for unit tests).

    Returns:
        The result of the operation.

    Raises:
        RateLimitOrDemandExceededError: When all max_attempts fail on 429 or 503.
        Exception: Any non-transient error is re-raised immediately without retries.
    """
    last_err: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as e:
            is_transient, err_type = is_transient_error(e)
            if not is_transient:
                raise

            last_err = e

            if attempt == max_attempts:
                error_msg = (
                    f"Exhausted maximum retry attempts ({max_attempts}) for model '{model_name}'. "
                    f"Last error: {err_type}. Details: {e}"
                )
                logger.error(error_msg)
                raise RateLimitOrDemandExceededError(error_msg) from e

            # Exponential backoff: base_delay * 2^(attempt - 1)
            backoff = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay = backoff + (random.uniform(0.1, 1.0) if jitter else 0.0)

            logger.warning(
                "Attempt %d/%d for model '%s' failed with %s. Retrying in %.2fs...",
                attempt,
                max_attempts,
                model_name,
                err_type,
                delay,
            )
            sleep_fn(delay)

    # Should not reach here, but guard against loop exhaustion
    msg = f"Exhausted {max_attempts} attempts for model '{model_name}' without result."
    raise RateLimitOrDemandExceededError(msg) from last_err
