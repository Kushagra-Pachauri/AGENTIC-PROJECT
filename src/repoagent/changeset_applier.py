"""Validated changeset applier for workspace files.

Safely applies proposed changes (unified diffs, snippet additions, file creations/deletions)
to the run workspace. Enforces workspace isolation:
  - Rejects absolute paths and drive-prefixed paths.
  - Rejects '..' traversal escaping the workspace.
  - Rejects unsupported operations and malformed diffs.
  - Protects the original demo_repo template from modification.
"""

from __future__ import annotations

import ast
import contextlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from repoagent.schemas import ChangeSet, FileChange

_SUPPORTED_ACTIONS = {"create", "modify", "delete"}


def validate_safe_path(workspace: Path, relative_path: str) -> Path:
    """Validate that *relative_path* stays strictly within *workspace*.

    Raises ValueError if the path is invalid, absolute, traverses out,
    or attempts to target demo_repo directly.
    """
    if not relative_path or not relative_path.strip():
        msg = "Relative file path cannot be empty."
        raise ValueError(msg)

    # Protect template repository
    ws = Path(workspace).resolve()
    if not ws.is_dir():
        msg = f"Workspace directory does not exist: {ws}"
        raise FileNotFoundError(msg)

    demo_repo_path = Path("demo_repo").resolve()
    if ws == demo_repo_path:
        msg = "Safety violation: Cannot apply changes directly to baseline 'demo_repo'."
        raise ValueError(msg)

    # Check for absolute or drive-rooted paths
    p = Path(relative_path)
    if (
        p.is_absolute()
        or bool(p.drive)
        or os.path.isabs(relative_path)
        or relative_path.startswith(("/", "\\"))
    ):
        msg = f"Absolute paths are forbidden: {relative_path!r}"
        raise ValueError(msg)

    # Check for traversal components
    if ".." in p.parts:
        msg = f"Path traversal forbidden: {relative_path!r}"
        raise ValueError(msg)

    resolved = (ws / relative_path).resolve()

    # Must be inside workspace and not workspace root itself
    try:
        resolved.relative_to(ws)
    except ValueError:
        msg = f"Path escapes workspace: {relative_path!r}"
        raise ValueError(msg) from None

    if resolved == ws:
        msg = "Target path cannot be the workspace root itself."
        raise ValueError(msg)

    # Symlink escape check
    real_target = Path(os.path.realpath(str(resolved)))
    real_ws = Path(os.path.realpath(str(ws)))
    try:
        real_target.relative_to(real_ws)
    except ValueError:
        msg = f"Symlink escape detected: {relative_path!r} resolves outside workspace."
        raise ValueError(msg) from None

    # Protected paths check
    from repoagent.security.tool_guardrails import is_protected_path

    is_prot, prot_reason = is_protected_path(relative_path)
    if is_prot:
        msg = f"Protected path cannot be modified: {relative_path!r} ({prot_reason})"
        raise ValueError(msg)

    return resolved


def extract_methods_from_lines(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Extract individual method definitions (name, lines) from a block of code."""
    methods: list[tuple[str, list[str]]] = []
    current_method: str | None = None
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        m = re.match(r"def\s+(\w+)\s*\(", stripped)
        if m:
            if current_method and current_lines:
                methods.append((current_method, current_lines))
            current_method = m.group(1)
            current_lines = [line]
        elif current_method is not None:
            if stripped.startswith("class ") or stripped.startswith("if __name__"):
                methods.append((current_method, current_lines))
                current_method = None
                current_lines = []
            else:
                current_lines.append(line)
    if current_method and current_lines:
        methods.append((current_method, current_lines))
    return methods


def clean_and_inject_methods(
    class_name: str, original_code: str, raw_added_lines: list[str]
) -> str:
    """Safely inject only new/missing methods into class_name without breaking syntax."""
    needed_imports: list[str] = []
    needed_globals: list[str] = []
    for line in raw_added_lines:
        s = line.strip()
        # Avoid invalid relative imports
        if s.startswith(("from ..", "from .", "import .")):
            continue
        if (s.startswith("import ") or s.startswith("from ")) and s not in original_code:
            needed_imports.append(s)
        if re.match(r"^[A-Z_0-9]+\s*=\s*", s) and s not in original_code:
            needed_globals.append(s)

    code = original_code
    lines = code.splitlines()
    if needed_imports:
        insert_idx = 0
        for i, item in enumerate(lines[:15]):
            if item.startswith(("from __future__", "import ", "from ")):
                insert_idx = i + 1
        lines = lines[:insert_idx] + needed_imports + lines[insert_idx:]

    if needed_globals:
        class_pattern = rf"class\s+{class_name}\b"
        for i, item in enumerate(lines):
            if re.search(class_pattern, item):
                lines = lines[:i] + needed_globals + [""] + lines[i:]
                break

    code = "\n".join(lines)
    methods = extract_methods_from_lines(raw_added_lines)
    if not methods:
        return code

    pattern = rf"(class\s+{class_name}\b[^\n]*:)"
    if not re.search(pattern, code):
        return code

    lines = code.splitlines()
    class_line_idx = -1
    for i, line in enumerate(lines):
        if re.search(pattern, line):
            class_line_idx = i
            break

    insert_idx = len(lines)
    for i in range(class_line_idx + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith(" ") and not line.startswith("\t"):
            insert_idx = i
            break

    method_blocks: list[str] = []
    for meth_name, meth_lines in methods:
        if f"def {meth_name}" in "\n".join(lines[class_line_idx:insert_idx]):
            continue
        base_indent = len(meth_lines[0]) - len(meth_lines[0].lstrip())
        formatted: list[str] = []
        for ml in meth_lines:
            s = ml.rstrip()
            if not s:
                formatted.append("")
                continue
            cur_indent = len(s) - len(s.lstrip())
            rel_indent = max(0, cur_indent - base_indent)
            if s.lstrip().startswith("def "):
                formatted.append("    " + s.lstrip())
            else:
                formatted.append("        " + " " * rel_indent + s.lstrip())
        method_blocks.extend([*formatted, ""])

    if not method_blocks:
        return code

    new_lines = lines[:insert_idx] + [""] + method_blocks + lines[insert_idx:]
    result = "\n".join(new_lines) + "\n"
    try:
        ast.parse(result)
        return result
    except SyntaxError as exc:
        msg = f"Syntax error in injected class methods: {exc}"
        raise ValueError(msg) from exc


def apply_unified_diff(original: str, diff: str) -> str:
    """Apply a unified diff or code patch to *original* text.

    Supports:
      1. Standard unified diffs with '@@' hunk headers.
      2. Fuzzy context matching and fallback injection for class methods.
      3. Line-diff patches (lines prefixed with '+').
      4. Full replacement code when diff is a complete module.
    """
    if not diff or not diff.strip():
        msg = "Diff content cannot be empty."
        raise ValueError(msg)

    # Case 0: Full module replacement (no diff headers, complete Python code)
    stripped_diff = diff.strip()
    valid_prefixes = ('"""', "'''", "from __future__", "import ", "from ", "class ")
    if (
        not any(line.startswith(("@@", "---", "+++")) for line in diff.splitlines())
        and not any(line.startswith(("+", "-")) for line in diff.splitlines() if line.strip())
        and (stripped_diff.startswith(valid_prefixes) or len(diff.splitlines()) > 15)
    ):
        try:
            ast.parse(diff)
            return diff + ("\n" if not diff.endswith("\n") else "")
        except SyntaxError:
            pass

    diff_lines = diff.splitlines()

    # Case 1: Standard or lenient unified diff with @@ hunk headers
    if any(line.lstrip().startswith("@@") for line in diff_lines):
        clean_orig = [line.rstrip("\r\n") for line in original.splitlines(keepends=True)]
        hunks: list[dict[str, Any]] = []
        current_hunk: dict[str, Any] | None = None
        hunk_header_re = re.compile(r"^@@\s*-?(\d+)?(?:,\d+)?\s*\+?(\d+)?(?:,\d+)?\s*@@?")

        for line in diff_lines:
            s_line = line.lstrip()
            if s_line.startswith("@@"):
                if current_hunk:
                    hunks.append(current_hunk)
                m = hunk_header_re.match(s_line)
                current_hunk = {"header": line, "match": m, "lines": []}
            elif current_hunk is not None:
                current_hunk["lines"].append(line)
        if current_hunk:
            hunks.append(current_hunk)

        if not hunks:
            msg = "Malformed unified diff: no valid hunks found."
            raise ValueError(msg)

        res_lines = list(clean_orig)
        for hunk in hunks:
            old_lines: list[str] = []
            new_lines: list[str] = []
            pure_additions: list[str] = []

            for item in hunk["lines"]:
                if item.startswith("-"):
                    old_lines.append(item[1:])
                elif item.startswith("+"):
                    clean_item = item[1:]
                    new_lines.append(clean_item)
                    pure_additions.append(clean_item)
                elif item.startswith(" "):
                    old_lines.append(item[1:])
                    new_lines.append(item[1:])
                elif not item.startswith("\\"):
                    old_lines.append(item)
                    new_lines.append(item)

            # Match location 1: Exact line matching
            found_idx = -1
            if old_lines:
                for i in range(len(res_lines) - len(old_lines) + 1):
                    if res_lines[i : i + len(old_lines)] == old_lines:
                        found_idx = i
                        break

            # Match location 2: Stripped whitespace matching
            if found_idx == -1 and old_lines:
                stripped_old = [item.strip() for item in old_lines if item.strip()]
                if stripped_old:
                    for i in range(len(res_lines) - len(stripped_old) + 1):
                        window = [res_lines[i + k].strip() for k in range(len(stripped_old))]
                        if window == stripped_old:
                            found_idx = i
                            break

            # Match location 3: Hunk header line-number hint for pure additions
            if found_idx == -1 and not old_lines and hunk["match"] and hunk["match"].group(1):
                hint = max(0, int(hunk["match"].group(1)) - 1)
                found_idx = min(hint, len(res_lines))

            # Match location 4: Class method injection fallback if context failed
            if found_idx == -1:
                if not pure_additions:
                    msg = f"Failed to apply hunk context in unified diff:\n{hunk['header']}"
                    raise ValueError(msg)

                target_code = "\n".join(res_lines)
                methods = [item for item in pure_additions if item.strip().startswith("def ")]
                if methods:
                    class_match = re.search(r"class\s+(\w+)\b", target_code)
                    if class_match:
                        cls_name = class_match.group(1)
                        target_code = clean_and_inject_methods(
                            cls_name, target_code, pure_additions
                        )
                        res_lines = target_code.splitlines()
                        continue

                res_lines.extend(pure_additions)
                continue

            res_lines[found_idx : found_idx + len(old_lines)] = new_lines

        applied = "\n".join(res_lines) + ("\n" if original.endswith("\n") else "")
        return applied

    # Case 2: Code additions / patch lines prefixed with '+'
    added_lines: list[str] = []
    for line in diff_lines:
        trimmed = line.strip()
        if not trimmed or trimmed.startswith(("---", "+++")):
            continue
        if line.startswith("+"):
            cleaned = line[1:]
            if cleaned.lstrip().startswith("> "):
                idx = cleaned.find("> ")
                cleaned = cleaned[:idx] + cleaned[idx + 2 :]
            added_lines.append(cleaned)
        elif not line.startswith("-"):
            added_lines.append(line)

    if not added_lines:
        msg = "Malformed diff: No actionable additions found."
        raise ValueError(msg)

    methods = [item for item in added_lines if item.strip().startswith("def ")]
    class_match = re.search(r"class\s+(\w+)\b", original)
    if methods and class_match:
        cls_name = class_match.group(1)
        return clean_and_inject_methods(cls_name, original, added_lines)

    snippet = "\n" + "\n".join(added_lines).rstrip() + "\n"
    return original.rstrip() + snippet


def _is_test_file(relative_path: str) -> bool:
    """Check if relative_path targets a test file."""
    p = Path(relative_path).as_posix()
    return (
        p.startswith("tests/") or "/tests/" in p or p.endswith("_test.py") or p.startswith("test_")
    )


def _verify_baseline_tests_preserved(orig_text: str, new_text: str, diff_text: str) -> None:
    """Verify that baseline tests are never deleted or reduced; only additions allowed."""
    try:
        orig_tree = ast.parse(orig_text)
        new_tree = ast.parse(new_text)
    except SyntaxError:
        return

    orig_funcs = {
        node.name
        for node in ast.walk(orig_tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    new_funcs = {
        node.name
        for node in ast.walk(new_tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }

    missing_funcs = orig_funcs - new_funcs
    if missing_funcs:
        msg = (
            f"Safety violation: Baseline test functions cannot be deleted or modified: "
            f"{sorted(missing_funcs)}"
        )
        raise ValueError(msg)

    # In diff hunks for test files, ensure no test lines were deleted
    for line in diff_text.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            stripped = line[1:].strip()
            if stripped and not stripped.startswith(("#", '"""', "'''")) and stripped in orig_text:
                msg = (
                    f"Safety violation: Baseline test files cannot have lines deleted: {stripped!r}"
                )
                raise ValueError(msg)


def _atomic_write_text(target_file: Path, text: str, workspace: Path) -> None:
    """Safely and atomically write text to target_file using a tempfile and os.replace.

    Verifies symlinks, protected paths, and path containment at write time.
    """
    ws = Path(workspace).resolve()
    target = target_file.resolve()
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    # Check symlinks at write time
    if os.path.islink(target_file) or os.path.islink(target):
        from repoagent.security.tool_guardrails import GuardrailViolation

        raise GuardrailViolation(
            f"Symlink writing is forbidden: {target_file}",
            category="SYMLINK_ESCAPE",
            target_path=str(target_file),
        )
    if os.path.islink(parent) or (parent.exists() and parent.is_symlink()):
        from repoagent.security.tool_guardrails import GuardrailViolation

        raise GuardrailViolation(
            f"Parent directory is a symlink: {parent}",
            category="SYMLINK_ESCAPE",
            target_path=str(parent),
        )

    # Realpath containment check
    real_target = Path(os.path.realpath(str(target)))
    real_ws = Path(os.path.realpath(str(ws)))
    try:
        real_target.relative_to(real_ws)
    except ValueError:
        from repoagent.security.tool_guardrails import GuardrailViolation

        raise GuardrailViolation(
            f"Symlink escape detected: {target_file} resolves to {real_target} outside workspace.",
            category="SYMLINK_ESCAPE",
            target_path=str(target_file),
        ) from None

    fd, temp_path = tempfile.mkstemp(
        dir=parent,
        prefix=f".{target.name}.tmp_",
        text=True,
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(temp_path, target)
    except Exception:
        if os.path.exists(temp_path):
            with contextlib.suppress(Exception):
                os.unlink(temp_path)
        raise


def apply_file_change(workspace: Path, change: FileChange) -> Path:
    """Validate and apply a single FileChange to the workspace.

    Returns the Path to the modified, created, or deleted file.
    """
    from repoagent.security.changeset_validator import scan_content_for_secrets
    from repoagent.security.tool_guardrails import GuardrailViolation, is_protected_path

    # Re-validate target path and protected path status
    target_file = validate_safe_path(workspace, change.path)

    is_prot, prot_reason = is_protected_path(change.path)
    if is_prot:
        raise GuardrailViolation(
            f"Protected path cannot be modified: {change.path!r} ({prot_reason})",
            category="PROTECTED_PATH",
            target_path=change.path,
        )

    # Symlink check on existing target
    if os.path.islink(target_file) or (target_file.exists() and target_file.is_symlink()):
        raise GuardrailViolation(
            f"Target file is a symlink: {change.path}",
            category="SYMLINK_ESCAPE",
            target_path=change.path,
        )

    # Protect test fixtures from arbitrary destruction
    if change.path.endswith("conftest.py") and target_file.exists():
        return target_file

    action = change.action.lower()
    # Support 'add' as an alias for create/modify
    if action == "add":
        action = "modify" if target_file.exists() else "create"

    if action not in _SUPPORTED_ACTIONS:
        msg = (
            f"Unsupported operation '{change.action}' on '{change.path}'. "
            f"Allowed actions: {sorted(_SUPPORTED_ACTIONS)}"
        )
        raise ValueError(msg)

    is_test = _is_test_file(change.path)

    if action == "delete":
        if is_test and target_file.exists():
            msg = f"Safety violation: Baseline test file '{change.path}' cannot be deleted."
            raise ValueError(msg)
        if target_file.exists():
            target_file.unlink()
        return target_file

    if not change.diff or not change.diff.strip():
        msg = f"Malformed diff: content is empty for action '{change.action}' on '{change.path}'."
        raise ValueError(msg)

    if action == "create":
        lines = change.diff.splitlines()
        if lines and all(item.startswith("+") or not item.strip() for item in lines):
            content = "\n".join(item[1:] if item.startswith("+") else item for item in lines)
        else:
            content = change.diff

        # Pre-write rendered secret scan
        has_secret, _sec_cat, sec_desc = scan_content_for_secrets(content, str(change.path))
        if has_secret:
            raise GuardrailViolation(
                f"Secret detected in rendered content for '{change.path}': {sec_desc}",
                category="SECRET_EXPOSURE",
                target_path=change.path,
            )

        if target_file.suffix == ".py":
            try:
                compile(content, str(target_file), "exec")
            except SyntaxError as exc:
                msg = f"Syntax validation failed for created file '{change.path}': {exc}"
                raise ValueError(msg) from exc

        _atomic_write_text(target_file, content, workspace)
        return target_file

    if not target_file.is_file():
        msg = f"Cannot modify non-existent file: {change.path!r}"
        raise ValueError(msg)

    orig_text = target_file.read_text(encoding="utf-8")
    new_text = apply_unified_diff(orig_text, change.diff)

    if action == "modify" and new_text == orig_text:
        msg = f"Malformed diff: applying diff to '{change.path}' produced no modifications."
        raise ValueError(msg)

    if is_test:
        _verify_baseline_tests_preserved(orig_text, new_text, change.diff)

    # Pre-write rendered secret scan
    has_secret, _sec_cat, sec_desc = scan_content_for_secrets(new_text, str(change.path))
    if has_secret:
        raise GuardrailViolation(
            f"Secret detected in rendered content for '{change.path}': {sec_desc}",
            category="SECRET_EXPOSURE",
            target_path=change.path,
        )

    if target_file.suffix == ".py":
        try:
            compile(new_text, str(target_file), "exec")
        except SyntaxError as exc:
            msg = f"Syntax validation failed for modified file '{change.path}': {exc}"
            raise ValueError(msg) from exc

    _atomic_write_text(target_file, new_text, workspace)
    return target_file


def apply_changeset_to_workspace(
    changeset: ChangeSet, workspace: Path, run_id: str | None = None
) -> list[Path]:
    """Apply all file changes in *changeset* to *workspace* with atomic rollback.

    Returns the list of modified/created file paths.
    Raises ValueError or GuardrailViolation if the changeset is empty or fails validation.
    If any change fails, all modified or created files are rolled back to their
    exact previous state.
    """
    if not changeset or not changeset.files:
        msg = "ChangeSet contains no files to apply."
        raise ValueError(msg)

    # 0. Comprehensive Step 7.4 ChangeSet & secret validation before touching disk
    from repoagent.security.changeset_validator import validate_changeset

    validate_changeset(changeset, workspace, run_id=run_id)

    # 1. Snapshot all targets before modifying anything
    snapshot: dict[Path, bytes | None] = {}
    for change in changeset.files:
        target_file = validate_safe_path(workspace, change.path)
        if target_file not in snapshot:
            if target_file.exists():
                snapshot[target_file] = target_file.read_bytes()
            else:
                snapshot[target_file] = None

    # 2. Apply each change, rolling back atomically on any failure
    applied_paths: list[Path] = []
    try:
        for change in changeset.files:
            path = apply_file_change(workspace, change)
            applied_paths.append(path)

        # 3. Enforce that no test file contains duplicate test functions
        from repoagent.workspace_utils import find_duplicate_test_functions

        duplicates = find_duplicate_test_functions(workspace)
        if duplicates:
            msg = (
                f"Safety violation: Duplicate test function detected: {', '.join(duplicates)}. "
                "Each test function must have a unique name."
            )
            raise ValueError(msg)

        return applied_paths
    except Exception as exc:
        # Atomic rollback: restore every file to original state
        for path, orig_bytes in snapshot.items():
            try:
                if orig_bytes is None:
                    if path.exists() or os.path.islink(path):
                        path.unlink()
                else:
                    path.write_bytes(orig_bytes)
            except Exception:
                pass
        raise exc


def workspace_has_modifications(workspace: Path) -> bool:
    """Check if the workspace has uncommitted changes compared to HEAD."""
    ws = Path(workspace).resolve()
    if not ws.is_dir():
        return False

    res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(ws),
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(res.stdout and res.stdout.strip())
