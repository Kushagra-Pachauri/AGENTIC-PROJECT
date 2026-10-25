"""Workspace utilities: cryptographic hashing, git diff extraction, and test validation."""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from repoagent.schemas import ChangeSet, FileChange, Plan

_EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    ".crewai_storage",
    ".pytest_crewai",
    ".ruff_cache",
}

_EXCLUDED_EXTS = {
    ".pyc",
    ".pyo",
    ".pyd",
}


def compute_workspace_hash(workspace: Path | str) -> str:
    """Compute deterministic SHA-256 hash of all source and test files in workspace."""
    ws = Path(workspace).resolve()
    if not ws.is_dir():
        return ""

    file_hashes: list[tuple[str, str]] = []

    for root, dirs, files in os.walk(ws):
        # Prune excluded directories in-place
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]

        for filename in files:
            ext = Path(filename).suffix.lower()
            if ext in _EXCLUDED_EXTS or filename.startswith("."):
                continue

            file_path = Path(root) / filename
            try:
                rel_path = file_path.relative_to(ws).as_posix()
                content = file_path.read_bytes()
                h = hashlib.sha256(content).hexdigest()
                file_hashes.append((rel_path, h))
            except (OSError, ValueError):
                continue

    file_hashes.sort(key=lambda x: x[0])

    hasher = hashlib.sha256()
    for rel_path, file_hash in file_hashes:
        hasher.update(f"{rel_path}:{file_hash}\n".encode())

    return hasher.hexdigest()


def find_duplicate_test_functions(workspace: Path | str) -> list[str]:
    """Find duplicate test function names within any class or module in workspace test files."""
    ws = Path(workspace).resolve()
    if not ws.is_dir():
        return []

    duplicates: list[str] = []

    for test_file in ws.rglob("test_*.py"):
        rel_path = test_file.relative_to(ws).as_posix()
        try:
            content = test_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(test_file))
        except (SyntaxError, OSError):
            continue

        # Check module-level test functions
        module_funcs: list[str] = []
        for node in tree.body:
            is_func = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            if is_func and node.name.startswith("test_"):
                if node.name in module_funcs:
                    duplicates.append(f"{rel_path}::{node.name}")
                else:
                    module_funcs.append(node.name)

        # Check class-level test methods
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_funcs: list[str] = []
                for item in node.body:
                    is_method = isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    if is_method and item.name.startswith("test_"):
                        if item.name in class_funcs:
                            duplicates.append(f"{rel_path}::{node.name}::{item.name}")
                        else:
                            class_funcs.append(item.name)

    return duplicates


def extract_changeset_from_disk(workspace: Path | str, summary: str = "") -> ChangeSet | None:
    """Extract actual modified, created, and deleted files and their git diffs from disk.

    Never relies on model-synthesized diffs. Includes new and untracked files.
    """
    ws = Path(workspace).resolve()
    if not ws.is_dir():
        return None

    # Track intent-to-add for untracked files so git diff shows them
    subprocess.run(["git", "add", "-N", "."], cwd=str(ws), capture_output=True, check=False)

    status_res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(ws),
        capture_output=True,
        text=True,
        check=False,
    )

    status_lines = [line.strip() for line in status_res.stdout.splitlines() if line.strip()]
    if not status_lines:
        return None

    file_changes: list[FileChange] = []

    for line in status_lines:
        # e.g. 'M  src/notes_app/app.py', 'A  new_file.py', '?? untracked.py', ' D old_file.py'
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        status_code, rel_path = parts[0], parts[1].strip()

        # Clean quotes if any
        if rel_path.startswith('"') and rel_path.endswith('"'):
            rel_path = rel_path[1:-1]

        # Ignore cache/temporary files
        is_exc_dir = any(exc in rel_path for exc in _EXCLUDED_DIRS)
        is_exc_ext = any(rel_path.endswith(ext) for ext in _EXCLUDED_EXTS)
        if is_exc_dir or is_exc_ext:
            continue

        if "D" in status_code:
            action = "delete"
        elif "A" in status_code or "?" in status_code:
            action = "create"
        else:
            action = "modify"

        diff_res = subprocess.run(
            ["git", "diff", "HEAD", "--", rel_path],
            cwd=str(ws),
            capture_output=True,
            text=True,
            check=False,
        )
        diff_text = diff_res.stdout

        # If file was created and git diff is empty, construct addition lines from file
        target_path = ws / rel_path
        if action == "create" and not diff_text.strip() and target_path.is_file():
            try:
                raw_text = target_path.read_text(encoding="utf-8")
                diff_text = "\n".join(f"+{line_text}" for line_text in raw_text.splitlines())
            except Exception:
                diff_text = "+[binary or unreadable file]"

        file_changes.append(
            FileChange(
                path=rel_path,
                action=action,
                diff=diff_text if diff_text.strip() else f"[{action} {rel_path}]",
            )
        )

    if not file_changes:
        return None

    return ChangeSet(
        files=file_changes,
        summary=summary or "Workspace modifications extracted from disk via git.",
    )


def validate_plan_paths(plan: Plan | None, workspace: Path | str) -> tuple[bool, list[str]]:
    """Validate that files in plan.files_to_change exist or follow package layout.

    Rules:
    - Every file being modified must exist in the workspace.
    - New files must lie strictly inside workspace and follow project package layout
      ('src/notes_app/...' or 'tests/...').
    - Absolute paths, drive roots, and '..' traversals are strictly forbidden.
    """
    ws = Path(workspace).resolve()
    errors: list[str] = []

    if not plan or not plan.files_to_change:
        return True, []

    for planned_file in plan.files_to_change:
        raw_path = planned_file.path.strip().replace("\\", "/")
        if not raw_path:
            errors.append("Planned file has empty path.")
            continue

        p = Path(raw_path)
        if p.is_absolute() or bool(p.drive) or raw_path.startswith("/") or ".." in p.parts:
            errors.append(
                f"Safety violation: Path traversal or absolute path forbidden: "
                f"'{planned_file.path}'"
            )
            continue

        target = (ws / raw_path).resolve()
        try:
            clean_rel = target.relative_to(ws).as_posix()
        except ValueError:
            errors.append(f"Safety violation: Path escapes workspace: '{planned_file.path}'")
            continue

        # 1. Existing file being modified
        if target.is_file():
            continue

        # 2. New file: must follow package layout
        if clean_rel.startswith("src/notes_app/") or clean_rel.startswith("tests/"):
            continue

        errors.append(
            f"Invalid file path in plan: '{planned_file.path}'. "
            f"File does not exist in workspace and does not follow project package layout "
            f"(expected 'src/notes_app/...' or 'tests/...')."
        )

    return len(errors) == 0, errors


def setup_workspace(source: Path | str, target: Path | str) -> Path:
    """Clone source repo into isolated target workspace and initialize git baseline."""
    src = Path(source).resolve()
    tgt = Path(target).resolve()
    if tgt.exists():
        import stat

        def _rm_readonly(func: object, p: str, _: object) -> None:
            os.chmod(p, stat.S_IWRITE)
            func(p)  # type: ignore[operator]

        shutil.rmtree(tgt, onerror=_rm_readonly)
    tgt.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, tgt)

    # Workspace hygiene: ensure .gitignore exists and ignores byte-code & caches
    gitignore_path = tgt / ".gitignore"
    gitignore_content = "__pycache__/\n*.py[cod]\n*$py.class\n.pytest_cache/\n*.pyc\n.ruff_cache/\n"
    if gitignore_path.exists():
        existing = gitignore_path.read_text(encoding="utf-8")
        if "__pycache__" not in existing:
            gitignore_path.write_text(existing + "\n" + gitignore_content, encoding="utf-8")
    else:
        gitignore_path.write_text(gitignore_content, encoding="utf-8")

    # Clean any accidental .pyc or __pycache__ copied over
    for pycache in tgt.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)
    for pyc in tgt.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)

    # Initialize git repo and make a baseline commit so git diff works afterwards
    subprocess.run(["git", "init"], cwd=str(tgt), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "RepoAgent"],
        cwd=str(tgt),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "repoagent@example.com"],
        cwd=str(tgt),
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=str(tgt), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline repo state"],
        cwd=str(tgt),
        check=True,
        capture_output=True,
    )
    return tgt
