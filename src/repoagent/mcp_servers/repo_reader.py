"""Repo Reader MCP Server.

Exposes filesystem tools scoped to a single workspace directory:
  - list_tree: recursive directory listing
  - read_file: read file contents (text)
  - search_code: grep-style code search

All paths are resolved relative to the workspace root.
Path traversal and absolute paths outside the workspace are rejected.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

_WORKSPACE: Path | None = None

mcp = FastMCP("RepoReader")


def _set_workspace(workspace: str) -> None:
    """Set the workspace root directory (call before running the server)."""
    global _WORKSPACE
    _WORKSPACE = Path(workspace).resolve()
    if not _WORKSPACE.is_dir():
        msg = f"Workspace directory does not exist: {_WORKSPACE}"
        raise FileNotFoundError(msg)


def _resolve_safe(relative_path: str) -> Path:
    """Resolve *relative_path* against the workspace, rejecting traversal.

    Raises:
        ValueError: if the resolved path escapes the workspace.
    """
    if _WORKSPACE is None:
        msg = "Workspace not configured — set REPOAGENT_WORKSPACE env var."
        raise RuntimeError(msg)

    # Reject absolute paths and drive-relative paths (Windows and POSIX)
    p = Path(relative_path)
    is_absolute = (
        p.is_absolute()
        or bool(p.drive)
        or os.path.isabs(relative_path)
        or relative_path.startswith(("/", "\\"))
    )
    if is_absolute:
        msg = f"Absolute paths are not allowed: {relative_path}"
        raise ValueError(msg)

    resolved = (_WORKSPACE / relative_path).resolve()

    # Must be inside workspace
    try:
        resolved.relative_to(_WORKSPACE)
    except ValueError:
        msg = f"Path traversal detected: {relative_path!r} resolves outside workspace."
        raise ValueError(msg) from None

    # Check symlink escapes
    real_target = Path(os.path.realpath(str(resolved)))
    real_ws = Path(os.path.realpath(str(_WORKSPACE)))
    try:
        real_target.relative_to(real_ws)
    except ValueError:
        msg = f"Symlink escape detected: {relative_path!r} resolves outside workspace."
        raise ValueError(msg) from None

    return resolved


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_tree(path: str = ".", max_depth: int = 4) -> str:
    """List the directory tree rooted at *path* (relative to workspace).

    Returns a text tree limited to *max_depth* levels.
    """
    root = _resolve_safe(path)
    if not root.is_dir():
        return f"Error: {path!r} is not a directory."

    lines: list[str] = []
    _walk(root, root, lines, depth=0, max_depth=max_depth)
    return "\n".join(lines) if lines else "(empty directory)"


def _walk(base: Path, current: Path, lines: list[str], depth: int, max_depth: int) -> None:
    if depth > max_depth:
        return
    indent = "  " * depth
    if current == base:
        lines.append(f"{current.name}/")
    else:
        lines.append(f"{indent}{current.name}/" if current.is_dir() else f"{indent}{current.name}")

    if current.is_dir():
        try:
            children = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            lines.append(f"{indent}  [permission denied]")
            return
        for child in children:
            # Skip common noise
            if child.name in {
                "__pycache__",
                ".git",
                "node_modules",
                ".venv",
                "venv",
                ".pytest_cache",
                ".ruff_cache",
                ".mypy_cache",
            }:
                continue
            _walk(base, child, lines, depth + 1, max_depth)


@mcp.tool()
def read_file(path: str, offset: int = 0, limit: int = 200) -> str:
    """Read the contents of a text file (relative to workspace).

    Args:
        path: Path to the file relative to workspace root.
        offset: 0-indexed line number to start reading from (default: 0).
        limit: Maximum number of lines to return (default: 200).

    Returns lines from offset to offset + limit. Binary files are rejected.
    """
    resolved = _resolve_safe(path)
    if not resolved.is_file():
        return f"Error: {path!r} is not a file."

    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading {path}: {exc}"

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    if offset < 0:
        offset = 0
    if limit <= 0:
        return ""
    if offset >= total_lines:
        return f"(empty: offset {offset} is beyond end of file with {total_lines} lines)"

    selected_lines = lines[offset : offset + limit]
    result = "".join(selected_lines)
    if offset + len(selected_lines) < total_lines:
        result += f"\n... (truncated: showing lines {offset + 1}-{offset + len(selected_lines)} of {total_lines})"
    return result


@mcp.tool()
def search_code(pattern: str, path: str = ".", max_results: int = 25) -> str:
    """Search for a regex *pattern* in files under *path* (relative to workspace).

    Returns matching lines as ``file:line_no: content``.
    """
    root = _resolve_safe(path)
    if not root.is_dir():
        return f"Error: {path!r} is not a directory."

    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return f"Invalid regex: {exc}"

    results: list[str] = []
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        # Skip binary / noise
        if file_path.suffix in {".pyc", ".pyo", ".so", ".dll", ".exe", ".png", ".jpg", ".gif"}:
            continue
        if any(
            part
            in {
                "__pycache__",
                ".git",
                "node_modules",
                ".venv",
                "venv",
                ".pytest_cache",
                ".ruff_cache",
                ".mypy_cache",
            }
            for part in file_path.parts
        ):
            continue

        try:
            text = file_path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, PermissionError):
            continue

        rel = file_path.relative_to(_WORKSPACE)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if compiled.search(line):
                results.append(f"{rel}:{line_no}: {line.rstrip()}")
                if len(results) >= max_results:
                    results.append(f"... (truncated at {max_results} results)")
                    return "\n".join(results)

    return "\n".join(results) if results else "No matches found."


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write text *content* to a file at *path* (relative to workspace).

    Creates parent directories if necessary.
    Rejects path traversal and absolute paths outside the workspace.
    Rejects writes to protected paths (.env, .github/workflows/, git metadata).
    """
    from repoagent.security.tool_guardrails import GuardrailViolation, is_protected_path

    is_prot, prot_reason = is_protected_path(path)
    if is_prot:
        raise GuardrailViolation(
            f"Cannot write to protected path: {prot_reason}",
            category="PROTECTED_PATH",
            tool_name="write_file",
            target_path=path,
        )

    resolved = _resolve_safe(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return f"Successfully wrote {len(content)} characters to {path}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    workspace = os.environ.get("REPOAGENT_WORKSPACE", "")
    if not workspace:
        print("Error: set REPOAGENT_WORKSPACE env var", file=sys.stderr)
        sys.exit(1)
    _set_workspace(workspace)
    mcp.run()
