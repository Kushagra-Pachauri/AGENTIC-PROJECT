"""Security and guardrail components for RepoAgent."""

from repoagent.security.changeset_validator import (
    ALLOWED_ACTIONS,
    MAX_FILE_SIZE_BYTES,
    MAX_FILES_CHANGED,
    MAX_TOTAL_DIFF_BYTES,
    scan_content_for_secrets,
    validate_changeset,
)
from repoagent.security.input_guardrails import (
    DetectionCategory,
    GuardrailDecision,
    InputSource,
    ScreeningResult,
    screen_external_content,
    screen_input,
    screen_repository_content,
    screen_user_requirement,
    wrap_untrusted_content,
)
from repoagent.security.tool_guardrails import (
    ALLOWED_COMMAND_PREFIXES,
    GuardrailViolation,
    is_protected_path,
    validate_command_allowlist,
    validate_path_containment,
    validate_sandbox_mount_policy,
    validate_tool_call,
)

__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_COMMAND_PREFIXES",
    "MAX_FILES_CHANGED",
    "MAX_FILE_SIZE_BYTES",
    "MAX_TOTAL_DIFF_BYTES",
    "DetectionCategory",
    "GuardrailDecision",
    "GuardrailViolation",
    "InputSource",
    "ScreeningResult",
    "is_protected_path",
    "scan_content_for_secrets",
    "screen_external_content",
    "screen_input",
    "screen_repository_content",
    "screen_user_requirement",
    "validate_changeset",
    "validate_command_allowlist",
    "validate_path_containment",
    "validate_sandbox_mount_policy",
    "validate_tool_call",
    "wrap_untrusted_content",
]
