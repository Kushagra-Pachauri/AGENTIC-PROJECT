"""Tests for capped back-off with jitter for 429/503 errors."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from repoagent.providers.retry import (
    RateLimitOrDemandExceededError,
    is_transient_error,
    retry_with_backoff,
)


class TestBackoffRetry:
    """Test suite for capped exponential backoff logic."""

    def test_transient_error_detection(self) -> None:
        assert is_transient_error(RuntimeError("429 RESOURCE_EXHAUSTED"))[0] is True
        assert is_transient_error(RuntimeError("503 UNAVAILABLE: High demand"))[0] is True
        assert is_transient_error(RuntimeError("Quota exceeded on free tier"))[0] is True
        assert is_transient_error(ValueError("400 Bad Request"))[0] is False
        assert is_transient_error(KeyError("missing_key"))[0] is False

    def test_success_on_first_attempt(self) -> None:
        mock_op = MagicMock(return_value="success_result")
        sleep_mock = MagicMock()

        result = retry_with_backoff(
            mock_op,
            model_name="gemini-3.6-flash",
            max_attempts=4,
            sleep_fn=sleep_mock,
        )

        assert result == "success_result"
        assert mock_op.call_count == 1
        assert sleep_mock.call_count == 0

    def test_success_after_transient_retries(self) -> None:
        sleep_mock = MagicMock()
        calls = [
            RuntimeError("503 UNAVAILABLE"),
            RuntimeError("429 RESOURCE_EXHAUSTED"),
            "recovered_value",
        ]

        def operation() -> str:
            item = calls.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        result = retry_with_backoff(
            operation,
            model_name="gemini-3.6-flash",
            max_attempts=4,
            base_delay=1.0,
            jitter=False,
            sleep_fn=sleep_mock,
        )

        assert result == "recovered_value"
        assert sleep_mock.call_count == 2
        # Backoff: 1.0, then 2.0
        assert sleep_mock.call_args_list[0][0][0] == 1.0
        assert sleep_mock.call_args_list[1][0][0] == 2.0

    def test_exhaustion_raises_rate_limit_exceeded_error(self) -> None:
        sleep_mock = MagicMock()
        mock_op = MagicMock(side_effect=RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded"))

        with pytest.raises(RateLimitOrDemandExceededError) as exc_info:
            retry_with_backoff(
                mock_op,
                model_name="gemini-3.6-flash",
                max_attempts=4,
                sleep_fn=sleep_mock,
            )

        assert "Exhausted maximum retry attempts (4)" in str(exc_info.value)
        assert "gemini-3.6-flash" in str(exc_info.value)
        assert "429 Rate Limit" in str(exc_info.value)
        assert mock_op.call_count == 4
        assert sleep_mock.call_count == 3

    def test_non_transient_error_fails_immediately(self) -> None:
        sleep_mock = MagicMock()
        mock_op = MagicMock(side_effect=ValueError("Invalid argument format"))

        with pytest.raises(ValueError, match="Invalid argument format"):
            retry_with_backoff(
                mock_op,
                model_name="gemini-3.6-flash",
                max_attempts=4,
                sleep_fn=sleep_mock,
            )

        assert mock_op.call_count == 1
        assert sleep_mock.call_count == 0
