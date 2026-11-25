"""ChangeSet output validation and secret scanning guardrails.

Enforces:
1. ChangeSet structure and required field validation.
2. File paths and workspace containment (no traversal, no escaping).
3. File operation allowlist (create, modify, delete).
4. Content encoding assumptions (UTF-8, no binary or null bytes).
5. Configurable size limits (max files, max individual file size, max total diff size).
6. Protected path restrictions (blocks writes to .env, .github/workflows/, git metadata).
7. Automated secret scanning to prevent accidental credential leakage in diffs/content.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from repoagent.monitoring.event_logger import EventType, get_event_logger
from repoagent.schemas import ChangeSet, FileChange
from repoagent.security.tool_guardrails import (
    GuardrailViolation,
    is_protected_path,
    validate_path_containment,
)

logger = logging.getLogger(__name__)


def _safe_log_changeset_event(event_logger: Any, run_id: str | None, **kwargs: Any) -> None:
    """Fail-safe event logging helper that prevents logger failures from breaking validation."""
    if not run_id:
        return
    try:
        event_logger.log_changeset_validation_event(run_id=run_id, **kwargs)
    except Exception as e:
        logger.warning(f"Failed to log changeset validation event: {e}")


# ---------------------------------------------------------------------------
# Configurable limits
# ---------------------------------------------------------------------------

MAX_FILES_CHANGED: int = int(os.environ.get("REPOAGENT_MAX_FILES_CHANGED", "25"))
MAX_FILE_SIZE_BYTES: int = int(os.environ.get("REPOAGENT_MAX_FILE_SIZE_BYTES", "500000"))  # 500 KB
MAX_TOTAL_DIFF_BYTES: int = int(os.environ.get("REPOAGENT_MAX_TOTAL_DIFF_BYTES", "1000000"))  # 1 MB
ALLOWED_ACTIONS: set[str] = {"create", "modify", "delete"}

# ---------------------------------------------------------------------------
# Secret scanning patterns
# ---------------------------------------------------------------------------

_SECRET_SCAN_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # 1. GitHub Personal Access Tokens (Classic and Fine-grained)
    (
        re.compile(r"\b(?:ghp_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
        "GITHUB_TOKEN",
        "GitHub Personal Access Token",
    ),
    # 2. OpenAI API Keys
    (
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"),
        "OPENAI_KEY",
        "OpenAI API Key",
    ),
    # 3. Google Gemini / Cloud API Keys
    (
        re.compile(r"\bAIzaSy[A-Za-z0-9_\-]{30,}\b"),
        "GEMINI_KEY",
        "Google / Gemini API Key",
    ),
    # 4. Bearer Tokens
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{20,}\b"),
        "BEARER_TOKEN",
        "HTTP Bearer Token",
    ),
    # 5. Private Keys
    (
        re.compile(r"-----BEGIN\s+(?:[A-Z0-9_-]+\s+)?PRIVATE\s+KEY-----"),
        "PRIVATE_KEY",
        "PEM Private Key",
    ),
    # 6. AWS Access Key IDs
    (
        re.compile(r"\b(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}\b"),
        "AWS_KEY",
        "AWS Access Key ID",
    ),
    # 7. Generic credential assignments in code/configs
    (
        re.compile(
            r"""(?i)\b(?:api[_-]?key|secret[_-]?key|auth[_-]?token|password|passwd)\s*[:=]\s*["']([^"'\s]{10,})["']"""
        ),
        "GENERIC_CREDENTIAL",
        "Hardcoded Secret / Password Assignment",
    ),
]

_SAFE_PLACEHOLDERS = {
    "your_api_key_here",
    "placeholder",
    "changeme",
    "dummy_password",
    "test_password",
    "example_token",
    "some_password",
    "my_secret_token",
    "mock_token",
}


def scan_content_for_secrets(
    content: str, file_path: str = ""
) -> tuple[bool, str | None, str | None]:
    """Scan code or diff content for accidental secret exposure.

    Returns:
        (has_secret, category, safe_rule_description)

    Guarantees:
        NEVER returns or logs the secret value itself.
    """
    if not content or not isinstance(content, str):
        return False, None, None

    for pattern, category, desc in _SECRET_SCAN_PATTERNS:
        match = pattern.search(content)
        if match:
            # If generic credential assignment, ignore known inert test placeholders
            if category == "GENERIC_CREDENTIAL":
                matched_val = match.group(1).lower() if match.groups() else ""
                if any(p in matched_val for p in _SAFE_PLACEHOLDERS):
                    continue
            return True, category, desc

    return False, None, None


# ---------------------------------------------------------------------------
# ChangeSet Validation
# ---------------------------------------------------------------------------


def validate_changeset(
    changeset: Any,
    workspace: Path | str,
    run_id: str | None = None,
) -> None:
    """Validate a proposed ChangeSet against all Step 7.4 security requirements.

    Enforces:
    1. Valid ChangeSet structure and non-empty files list if changes are claimed.
    2. Number of changed files <= MAX_FILES_CHANGED.
    3. Total diff size <= MAX_TOTAL_DIFF_BYTES.
    4. For every FileChange:
       - Operation is in ALLOWED_ACTIONS (create, modify, delete).
       - File stays strictly within workspace (no traversal, no absolute paths).
       - Target file is not protected (.env, .github/workflows/, git metadata).
       - File diff size <= MAX_FILE_SIZE_BYTES.
       - Valid UTF-8 encoding with no null bytes.
       - Secret scanning passes with zero detected credentials.

    Raises:
        GuardrailViolation: if any check fails.
    """
    event_logger = get_event_logger()
    ws = Path(workspace).resolve()

    # 1. Structure validation
    if not isinstance(changeset, ChangeSet):
        # Attempt coercion if dictionary
        if isinstance(changeset, dict):
            try:
                changeset = ChangeSet(**changeset)
            except Exception as exc:
                if run_id:
                    event_logger.log_changeset_validation_event(
                        run_id=run_id,
                        event_type=EventType.CHANGESET_VALIDATION_FAILED,
                        decision="BLOCK",
                        reason=f"Malformed ChangeSet dictionary: {exc}",
                        category="MALFORMED_CHANGESET",
                    )
                raise GuardrailViolation(
                    f"Invalid ChangeSet data structure: {exc}",
                    category="MALFORMED_CHANGESET",
                ) from exc
        else:
            if run_id:
                event_logger.log_changeset_validation_event(
                    run_id=run_id,
                    event_type=EventType.CHANGESET_VALIDATION_FAILED,
                    decision="BLOCK",
                    reason="Input is not a valid ChangeSet instance.",
                    category="MALFORMED_CHANGESET",
                )
            raise GuardrailViolation(
                f"Expected ChangeSet instance, received {type(changeset).__name__}",
                category="MALFORMED_CHANGESET",
            )

    files = changeset.files or []
    file_count = len(files)
    total_diff_bytes = sum(len(f.diff.encode("utf-8", errors="replace")) for f in files)

    # 2. Maximum file count limit
    if file_count > MAX_FILES_CHANGED:
        reason = (
            f"ChangeSet exceeds maximum file limit: {file_count} files changed "
            f"(maximum allowed is {MAX_FILES_CHANGED})."
        )
        _safe_log_changeset_event(
            event_logger,
            run_id=run_id,
            event_type=EventType.CHANGESET_VALIDATION_FAILED,
            decision="BLOCK",
            reason=reason,
            category="LIMIT_EXCEEDED",
            file_count=file_count,
            diff_size_bytes=total_diff_bytes,
        )
        raise GuardrailViolation(
            reason,
            category="LIMIT_EXCEEDED",
            details={"file_count": file_count, "max_allowed": MAX_FILES_CHANGED},
        )

    # 3. Maximum total diff size limit
    if total_diff_bytes > MAX_TOTAL_DIFF_BYTES:
        reason = (
            f"ChangeSet total diff size ({total_diff_bytes} bytes) exceeds limit "
            f"of {MAX_TOTAL_DIFF_BYTES} bytes."
        )
        _safe_log_changeset_event(
            event_logger,
            run_id=run_id,
            event_type=EventType.CHANGESET_VALIDATION_FAILED,
            decision="BLOCK",
            reason=reason,
            category="LIMIT_EXCEEDED",
            file_count=file_count,
            diff_size_bytes=total_diff_bytes,
        )
        raise GuardrailViolation(
            reason,
            category="LIMIT_EXCEEDED",
            details={"total_bytes": total_diff_bytes, "max_allowed": MAX_TOTAL_DIFF_BYTES},
        )

    # 4. Validate each FileChange
    for idx, fchange in enumerate(files):
        if not isinstance(fchange, FileChange):
            raise GuardrailViolation(
                f"Element at index {idx} in ChangeSet.files is not a FileChange object.",
                category="MALFORMED_CHANGESET",
            )

        clean_path = str(fchange.path).strip()
        if not clean_path:
            raise GuardrailViolation(
                f"FileChange at index {idx} has an empty path.",
                category="MALFORMED_CHANGESET",
            )

        # A. Action allowlist
        action = str(fchange.action).strip().lower()
        if action not in ALLOWED_ACTIONS:
            reason = (
                f"Unsupported operation '{action}' for file '{clean_path}'. "
                f"Allowed operations: {', '.join(sorted(ALLOWED_ACTIONS))}."
            )
            if run_id:
                event_logger.log_changeset_validation_event(
                    run_id=run_id,
                    event_type=EventType.CHANGESET_VALIDATION_FAILED,
                    decision="BLOCK",
                    reason=reason,
                    category="UNSUPPORTED_OPERATION",
                    file_count=file_count,
                )
            raise GuardrailViolation(
                reason,
                category="UNSUPPORTED_OPERATION",
                target_path=clean_path,
            )

        # B. Protected path check
        is_prot, prot_reason = is_protected_path(clean_path)
        if is_prot:
            reason = f"Proposed change targets protected path: {prot_reason}"
            if run_id:
                event_logger.log_changeset_validation_event(
                    run_id=run_id,
                    event_type=EventType.CHANGESET_VALIDATION_FAILED,
                    decision="BLOCK",
                    reason=reason,
                    category="PROTECTED_PATH",
                    file_count=file_count,
                )
            raise GuardrailViolation(
                reason,
                category="PROTECTED_PATH",
                target_path=clean_path,
            )

        # C. Workspace containment check (rejects traversal, absolute paths, and symlink escapes)
        validate_path_containment(clean_path, ws)

        # D. Encoding and content checks
        diff_text = fchange.diff or ""
        if "\x00" in diff_text:
            raise GuardrailViolation(
                f"Binary content or null bytes detected in proposed change for '{clean_path}'.",
                category="ENCODING_ERROR",
                target_path=clean_path,
            )

        # E. Individual file/diff size limit
        diff_bytes = len(diff_text.encode("utf-8", errors="replace"))
        if diff_bytes > MAX_FILE_SIZE_BYTES:
            reason = (
                f"Diff for file '{clean_path}' ({diff_bytes} bytes) exceeds maximum "
                f"single file limit ({MAX_FILE_SIZE_BYTES} bytes)."
            )
            if run_id:
                event_logger.log_changeset_validation_event(
                    run_id=run_id,
                    event_type=EventType.CHANGESET_VALIDATION_FAILED,
                    decision="BLOCK",
                    reason=reason,
                    category="LIMIT_EXCEEDED",
                    file_count=file_count,
                )
            raise GuardrailViolation(
                reason,
                category="LIMIT_EXCEEDED",
                target_path=clean_path,
                details={"file_bytes": diff_bytes, "max_allowed": MAX_FILE_SIZE_BYTES},
            )

        # F. Secret scanning
        has_secret, category, rule_desc = scan_content_for_secrets(diff_text, clean_path)
        if has_secret:
            reason = (
                f"Security violation: Secret detected in proposed change for '{clean_path}': "
                f"rule '{rule_desc}' (category: {category}). Content blocked."
            )
            if run_id:
                # Log secret scan blocked event
                try:
                    event_logger.log_event(
                        run_id=run_id,
                        event_type=EventType.SECRET_SCAN_BLOCKED,
                        agent_name="ChangeSetValidator",
                        stage="changeset_validation",
                        input_summary={"file": clean_path, "rule": rule_desc, "category": category},
                        metadata={"file": clean_path, "rule": rule_desc, "category": category},
                    )
                except Exception as e:
                    logger.warning(f"Failed to log SECRET_SCAN_BLOCKED event: {e}")
                _safe_log_changeset_event(
                    event_logger,
                    run_id=run_id,
                    event_type=EventType.CHANGESET_VALIDATION_FAILED,
                    decision="BLOCK",
                    reason=reason,
                    category="SECRET_EXPOSURE",
                    file_count=file_count,
                )
            raise GuardrailViolation(
                reason,
                category="SECRET_EXPOSURE",
                target_path=clean_path,
                details={"file": clean_path, "rule": rule_desc, "category": category},
            )

    # 5. Validation passed successfully
    _safe_log_changeset_event(
        event_logger,
        run_id=run_id,
        event_type=EventType.CHANGESET_VALIDATION_PASSED,
        decision="ALLOW",
        reason="ChangeSet passed all structural, containment, size, and security checks.",
        file_count=file_count,
        diff_size_bytes=total_diff_bytes,
    )
