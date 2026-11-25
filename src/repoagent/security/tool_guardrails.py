"""Pre-execution tool execution guardrails and workspace containment.

Enforces:
1. Command allowlist for shell/subprocess execution tools.
2. Strict workspace containment (rejects traversal, absolute paths, and symlink escapes).
3. Protected path restrictions (blocks writes to .env, .github/workflows/,
   git metadata, and credentials).
4. Structured GuardrailViolation exception with fail-safe logging.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from repoagent.monitoring.event_logger import EventType, get_event_logger

logger = logging.getLogger(__name__)


class GuardrailViolation(ValueError):  # noqa: N818
    """Structured security exception raised when a tool call or changeset is blocked."""

    def __init__(
        self,
        reason: str,
        category: str = "TOOL_GUARD",
        tool_name: str | None = None,
        target_path: str | None = None,
        command: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.category = category
        self.tool_name = tool_name
        self.target_path = target_path
        self.command = command
        self.details = details or {}

    def __str__(self) -> str:
        ctx = []
        if self.tool_name:
            ctx.append(f"tool='{self.tool_name}'")
        if self.target_path:
            ctx.append(f"path='{self.target_path}'")
        if self.command:
            ctx.append(f"command='{self.command}'")
        ctx_str = f" ({', '.join(ctx)})" if ctx else ""
        return f"[{self.category}] {self.reason}{ctx_str}"


# ---------------------------------------------------------------------------
# Command allowlist & destructive command patterns
# ---------------------------------------------------------------------------

ALLOWED_COMMAND_PREFIXES: tuple[str, ...] = (
    "pytest",
    "ruff",
    "npm test",
    "eslint",
)

_DESTRUCTIVE_COMMAND_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)\brm\s+-(?:rf?|fr?)\b"),
        "Destructive recursive deletion command (rm -rf)",
    ),
    (
        re.compile(r"(?i)\brmdir\s+/[sq]\b"),
        "Destructive directory deletion command (rmdir /s)",
    ),
    (
        re.compile(r"(?i)\bdel\s+/[sqfa]\b"),
        "Destructive mass file deletion command (del /s)",
    ),
    (
        re.compile(r"(?i)\bformat\s+[a-z]:\b"),
        "Destructive disk format command",
    ),
    (
        re.compile(r"(?i)\bmkfs(?:\.[a-z0-9]+)?\b"),
        "Destructive filesystem creation command",
    ),
    (
        re.compile(r"(?i)\bdd\s+if="),
        "Low-level disk overwrite command (dd)",
    ),
    (
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        "Fork bomb command",
    ),
    (
        re.compile(r"(?i)\b(?:curl|wget|nc|netcat)\b"),
        "Unapproved network exfiltration tool",
    ),
    (
        re.compile(r"(?i)\b(?:chmod\s+-R\s+777|chown)\b"),
        "Unrestricted permission manipulation command",
    ),
    (
        re.compile(r"(?i)\b(?:shutdown|reboot|poweroff|init\s+0)\b"),
        "System shutdown or reboot command",
    ),
]

_SHELL_METACHAR_PATTERN = re.compile(r"[;&|`$><\n\r]")


def validate_command_allowlist(command: str) -> str:
    """Validate a command against the strict allowlist and destructive command checks.

    Raises:
        GuardrailViolation: if the command is empty, contains shell metacharacters,
                           matches destructive patterns, or is not allowlisted.
    """
    stripped = command.strip()
    if not stripped:
        raise GuardrailViolation(
            "Empty command is not permitted.",
            category="COMMAND_ALLOWLIST",
            command=command,
        )

    # 1. Reject shell metacharacters / command chaining
    if _SHELL_METACHAR_PATTERN.search(stripped):
        raise GuardrailViolation(
            f"Command not allowed: Shell metacharacters not allowed in command: {stripped!r}",
            category="COMMAND_CHAINING",
            command=stripped,
        )

    # 2. Reject explicitly destructive commands
    for pattern, desc in _DESTRUCTIVE_COMMAND_PATTERNS:
        if pattern.search(stripped):
            raise GuardrailViolation(
                f"Command not allowed (destructive command blocked): {desc}",
                category="DESTRUCTIVE_COMMAND",
                command=stripped,
            )

    # 3. Check allowlisted command prefixes
    is_allowed = any(
        stripped == allowed or stripped.startswith(f"{allowed} ")
        for allowed in ALLOWED_COMMAND_PREFIXES
    )
    if not is_allowed:
        allowed_str = ", ".join(ALLOWED_COMMAND_PREFIXES)
        raise GuardrailViolation(
            f"Command not allowed: {stripped!r}. Allowed prefixes: {allowed_str}.",
            category="UNAPPROVED_COMMAND",
            command=stripped,
        )

    return stripped


# ---------------------------------------------------------------------------
# Path containment and protected paths
# ---------------------------------------------------------------------------

_PROTECTED_DIR_PREFIXES: tuple[str, ...] = (
    ".github/workflows",
    ".git",
)

_PROTECTED_FILE_NAMES: set[str] = {
    ".env",
    "credentials",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
}


def is_protected_path(path: str | Path) -> tuple[bool, str | None]:
    """Check if *path* refers to a protected/sensitive path that must not be modified.

    Returns:
        (is_protected, reason_if_protected)
    """
    clean_p = str(path).replace("\\", "/").strip().lower()
    p = Path(clean_p)
    file_name = p.name

    # 1. Environment and secret files
    if (
        file_name in _PROTECTED_FILE_NAMES
        or file_name.startswith(".env.")
        or file_name.endswith(".env")
    ):
        return True, f"Environment/secret file '{p.name}' is protected from modification"

    # 2. Key and certificate files
    if p.suffix in (".pem", ".key", ".pkcs12", ".pfx"):
        return True, f"Certificate/key file '{p.name}' is protected from modification"

    # 3. Directory containment: .github/workflows and .git
    parts = p.parts
    if len(parts) >= 2 and parts[0] == ".github" and parts[1] == "workflows":
        return True, "GitHub workflow directory '.github/workflows' is protected"

    if parts and parts[0] == ".git":
        return True, "Git repository control metadata in '.git/' is protected"

    for prefix in _PROTECTED_DIR_PREFIXES:
        if clean_p == prefix or clean_p.startswith(f"{prefix}/"):
            return True, f"Directory '{prefix}' is protected from modification"

    return False, None


def validate_path_containment(
    path: str | Path, workspace: Path | str, allow_root: bool = False
) -> Path:
    """Validate that *path* resolves safely within *workspace* without escaping.

    Enforces:
    - Non-empty path.
    - Rejection of absolute paths and drive letters.
    - Rejection of '..' directory traversal.
    - Path must stay inside workspace.
    - Target cannot be the workspace root itself (unless allow_root is True).
    - Rejection of symlink escapes.

    Returns:
        Resolved absolute Path inside workspace.

    Raises:
        GuardrailViolation: if any containment check fails.
    """
    rel_str = str(path).strip()
    if not rel_str:
        raise GuardrailViolation("File path cannot be empty.", category="PATH_CONTAINMENT")

    ws = Path(workspace).resolve()
    if not ws.is_dir():
        raise GuardrailViolation(
            f"Workspace directory does not exist: {ws}",
            category="WORKSPACE_ERROR",
        )

    # Protect template baseline
    demo_repo = Path("demo_repo").resolve()
    if ws == demo_repo:
        raise GuardrailViolation(
            "Safety violation: Cannot operate directly on baseline 'demo_repo'.",
            category="BASELINE_PROTECTION",
        )

    # Reject absolute and drive-rooted paths
    p = Path(rel_str)
    if (
        p.is_absolute()
        or bool(p.drive)
        or os.path.isabs(rel_str)
        or rel_str.startswith(("/", "\\"))
    ):
        raise GuardrailViolation(
            f"Absolute paths are forbidden: {rel_str!r}",
            category="PATH_TRAVERSAL",
            target_path=rel_str,
        )

    # Reject traversal tokens
    if ".." in p.parts:
        raise GuardrailViolation(
            f"Path traversal forbidden: {rel_str!r}",
            category="PATH_TRAVERSAL",
            target_path=rel_str,
        )

    resolved = (ws / rel_str).resolve()

    # Verify containment within workspace
    try:
        resolved.relative_to(ws)
    except ValueError:
        raise GuardrailViolation(
            f"Path traversal detected: {rel_str!r} resolves outside workspace.",
            category="PATH_TRAVERSAL",
            target_path=rel_str,
        ) from None

    if resolved == ws and not allow_root:
        raise GuardrailViolation(
            "Target path cannot be the workspace root itself.",
            category="PATH_CONTAINMENT",
            target_path=rel_str,
        )

    # Symlink escape check: check realpath of target and intermediate parents
    real_target = Path(os.path.realpath(str(resolved)))
    real_ws = Path(os.path.realpath(str(ws)))
    try:
        real_target.relative_to(real_ws)
    except ValueError:
        raise GuardrailViolation(
            f"Symlink escape detected: {rel_str!r} resolves to {real_target} outside workspace.",
            category="SYMLINK_ESCAPE",
            target_path=rel_str,
        ) from None

    return resolved


# ===========================================================================
# Execution Policy for Untrusted Package Scripts (e.g., npm test)
# ===========================================================================
# Untrusted repository package scripts (such as scripts defined in package.json
# triggered via 'npm test') are strictly sandboxed under the following trust model:
# 1. Sandboxed Execution Only: Commands such as 'npm test' MUST never run on the host;
#    they must execute inside the isolated Docker sandbox (non-root 'sandbox' user).
# 2. Complete Network Isolation: The sandbox container must have network_mode="none"
#    preventing any outbound data exfiltration or inbound command-and-control.
# 3. Mount Restrictions: Only the dedicated workspace directory may be mounted.
#    Host credential directories (~/.ssh, ~/.aws), environment secret files (.env),
#    and container engine sockets (/var/run/docker.sock) are strictly forbidden from mounts.
# 4. Resource Limits: Nano-CPUs and memory limits are strictly enforced.

_FORBIDDEN_MOUNT_SUBSTRINGS: tuple[str, ...] = (
    ".ssh",
    ".aws",
    ".env",
    "docker.sock",
    "docker_engine",
    "/etc",
    "/var/run",
    "windows\\system32",
)


def validate_sandbox_mount_policy(
    workspace: Path | str,
    volumes: dict[str, Any] | None = None,
    network_mode: str = "none",
) -> None:
    """Validate sandbox container isolation constraints before execution.

    Enforces:
    1. network_mode must be strictly 'none'.
    2. Volumes must only mount the approved workspace directory.
    3. Host credentials (~/.ssh, ~/.aws, .env), Docker sockets (/var/run/docker.sock),
       and sensitive directories are strictly forbidden.

    Raises:
        GuardrailViolation: if any sandbox isolation rule is violated.
    """
    if network_mode != "none":
        raise GuardrailViolation(
            f"Sandbox security violation: network_mode must be 'none', got '{network_mode}'.",
            category="SANDBOX_POLICY",
        )

    ws = Path(workspace).resolve()
    if volumes:
        for host_path_str in volumes:
            clean_host = str(host_path_str).replace("/", "\\").lower()
            for forbidden in _FORBIDDEN_MOUNT_SUBSTRINGS:
                if forbidden.lower() in clean_host:
                    raise GuardrailViolation(
                        f"Sandbox security violation: forbidden host path '{host_path_str}' "
                        f"mounted. Matched sensitive pattern: '{forbidden}'.",
                        category="SANDBOX_POLICY",
                    )
            host_path = Path(host_path_str).resolve()
            if host_path != ws:
                raise GuardrailViolation(
                    f"Sandbox security violation: unauthorized host path '{host_path_str}' "
                    f"mounted. Only approved workspace directory '{ws}' may be mounted.",
                    category="SANDBOX_POLICY",
                )


# ---------------------------------------------------------------------------
# Central Tool Call Validation
# ---------------------------------------------------------------------------

_COMMAND_TOOLS = {
    "run_tests",
    "run_lint",
    "run_command",
    "execute_command",
    "shell",
    "bash",
    "exec",
    "terminal",
    "subprocess",
}

_PATH_TOOLS = {
    "read_file",
    "write_file",
    "save_file",
    "delete_file",
    "list_tree",
    "search_code",
}

_WRITE_TOOLS = {
    "write_file",
    "save_file",
    "delete_file",
    "apply_changeset",
}


def sanitize_tool_name(name: str) -> str:
    """Sanitize and strip special/channel tokens from tool names.

    E.g. 'read_file<|channel|>commentary' -> 'read_file'
         'read_file<|call|>' -> 'read_file'
    """
    if not name:
        return ""
    cleaned = re.sub(r"<\|.*?\|>.*", "", str(name))
    cleaned = re.sub(r"<\|.*", "", cleaned)
    return cleaned.strip()


def validate_tool_call(
    tool_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    workspace: Path | str | None = None,
    run_id: str | None = None,
) -> bool:
    """Centralized tool execution guard that validates tool calls immediately before execution.

    Enforces:
    - Tool name sanitization and allowlist rejection for malformed/unknown tools.
    - Command allowlist on command-execution tools.
    - Workspace containment on filesystem tools.
    - Protected path restrictions on write tools.
    - Security event logging to EventLogger.

    Returns:
        True if the tool call passes all security guardrails.

    Raises:
        GuardrailViolation: if any security rule is violated.
    """
    clean_tool = sanitize_tool_name(tool_name).lower()
    event_logger = get_event_logger()

    allowed_tools = _COMMAND_TOOLS | _PATH_TOOLS | _WRITE_TOOLS | {
        "read_repository_file",
        "list_repository_tree",
        "search_repository_code",
    }
    if not clean_tool or clean_tool not in allowed_tools:
        raise GuardrailViolation(
            f"Execution blocked: unknown or malformed tool name {tool_name!r}.",
            category="UNKNOWN_TOOL",
            tool_name=tool_name,
        )

    try:
        # 1. Command execution checks
        if clean_tool in _COMMAND_TOOLS:
            cmd = kwargs.get("command") or kwargs.get("cmd")
            if not cmd and args:
                cmd = args[0]
            if isinstance(cmd, str):
                validate_command_allowlist(cmd)

        # 2. Filesystem path checks
        if clean_tool in _PATH_TOOLS or clean_tool in _WRITE_TOOLS:
            target_p = kwargs.get("path") or kwargs.get("file_path")
            if not target_p and args:
                target_p = args[0]

            # Missing workspace context cannot authorize filesystem operations
            if not workspace:
                raise GuardrailViolation(
                    f"Filesystem tool '{tool_name}' requires an active verified workspace context.",
                    category="WORKSPACE_REQUIRED",
                    tool_name=tool_name,
                    target_path=str(target_p) if target_p else None,
                )

            if target_p and isinstance(target_p, str | Path):
                # Protected paths check on writes
                if clean_tool in _WRITE_TOOLS:
                    is_prot, reason = is_protected_path(target_p)
                    if is_prot:
                        raise GuardrailViolation(
                            f"Write operation blocked on protected path: {reason}",
                            category="PROTECTED_PATH",
                            tool_name=tool_name,
                            target_path=str(target_p),
                        )

                # Workspace containment check (allow root query for read-only tree/search)
                allow_root = clean_tool in ("list_tree", "search_code") and str(
                    target_p
                ).strip() in (".", "./", "")
                validate_path_containment(target_p, workspace, allow_root=allow_root)

        # Log TOOL_GUARD_PASSED event if run_id is active
        if run_id:
            try:
                event_logger.log_tool_guard_event(
                    run_id=run_id,
                    event_type=EventType.TOOL_GUARD_PASSED,
                    tool_name=tool_name,
                    decision="ALLOW",
                    reason=f"Tool call '{tool_name}' passed pre-execution security checks.",
                    category="TOOL_GUARD",
                    metadata={"args_count": len(args), "kwargs_keys": list(kwargs.keys())},
                )
            except Exception as e:
                logger.warning(f"Failed to log TOOL_GUARD_PASSED event: {e}")
        return True

    except GuardrailViolation as gv:
        if run_id:
            try:
                event_logger.log_tool_guard_event(
                    run_id=run_id,
                    event_type=EventType.TOOL_GUARD_BLOCKED,
                    tool_name=tool_name,
                    decision="BLOCK",
                    reason=gv.reason,
                    category=gv.category,
                    metadata={"target_path": gv.target_path, "command": gv.command},
                )
            except Exception as e:
                logger.warning(f"Failed to log TOOL_GUARD_BLOCKED event: {e}")
        raise
