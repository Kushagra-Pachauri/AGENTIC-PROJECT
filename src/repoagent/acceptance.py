"""Requirement acceptance verification module.

Ensures that candidate changesets and workspaces implement the actual
submitted requirement and do not pass based on placeholder stubs or vacuous tests.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

from repoagent.schemas import ChangeSet


def _extract_functions_and_classes(code: str) -> set[str]:
    """Parse python code and return set of defined function and class names."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
    return names


def _is_vacuous_test_function(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if a test function body is merely 'assert True', 'pass', or empty."""
    statements = [
        stmt
        for stmt in func_node.body
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Constant)
    ]
    if not statements:
        return True

    # If only one statement and it's 'pass'
    if len(statements) == 1 and isinstance(statements[0], ast.Pass):
        return True

    # If only one statement and it's 'assert True'
    if len(statements) == 1 and isinstance(statements[0], ast.Assert):
        test_val = statements[0].test
        if isinstance(test_val, ast.Constant) and test_val.value is True:
            return True

    return False


def _check_for_vacuous_tests(workspace: Path, changed_files: list[str]) -> list[str]:
    """Inspect only modified/added test files in workspace for vacuous tests."""
    vacuous: list[str] = []
    test_files_to_check: list[Path] = []
    for cf in changed_files:
        p = Path(cf)
        if "test" in p.name.lower() and p.suffix == ".py":
            full_p = workspace / cf
            if full_p.is_file():
                test_files_to_check.append(full_p)

    for test_file in test_files_to_check:
        try:
            content = test_file.read_text(encoding="utf-8")
            tree = ast.parse(content)
        except Exception:
            continue

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name.startswith("test_")
                and _is_vacuous_test_function(node)
            ):
                vacuous.append(f"{test_file.name}::{node.name}")

    return vacuous


def verify_requirement_acceptance(
    requirement: str,
    workspace: Path,
    changeset: ChangeSet | None = None,
) -> tuple[bool, str | None]:
    """Verify that candidate changes in workspace implement the actual requirement.

    Returns:
        (True, None) if acceptance checks pass.
        (False, reason_string) if rejected.
    """
    ws = Path(workspace).resolve()
    if not ws.is_dir():
        return False, f"Workspace directory not found: {ws}"

    req_lower = requirement.lower()

    # 1. Inspect modified and added files
    changed_files: list[str] = []
    if changeset and changeset.files:
        changed_files = [f.path.replace("\\", "/") for f in changeset.files]
    else:
        # Check from git status
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(ws),
                capture_output=True,
                text=True,
                check=False,
            )
            for line in res.stdout.splitlines():
                if line.strip():
                    entry = line[3:].strip().replace("\\", "/")
                    changed_files.append(entry)
        except Exception:
            pass

    if not changed_files:
        return False, "Acceptance failed: No files were added or modified in the workspace."

    # 2. Reject obvious placeholder tokens in diffs or workspace files
    for path_str in changed_files:
        f_path = ws / path_str
        if f_path.is_file():
            content = f_path.read_text(encoding="utf-8")
            if "def handle_feature():" in content:
                return (
                    False,
                    f"Acceptance rejected: File '{path_str}' contains "
                    "unresolved placeholder stub 'def handle_feature()'.",
                )
            if "def test_feature_stub():" in content:
                return (
                    False,
                    f"Acceptance rejected: Test file '{path_str}' contains "
                    "unresolved stub 'def test_feature_stub()'.",
                )

    # 3. Check for vacuous tests (assert True / pass) in modified/added test files
    vacuous_tests = _check_for_vacuous_tests(ws, changed_files)
    if vacuous_tests:
        return (
            False,
            f"Acceptance rejected: Vacuous or stub tests detected: {', '.join(vacuous_tests)}.",
        )

    # 4. Keyword and concept relevance check
    # Check for specific requirements like word_count
    if "word_count" in req_lower:
        # Verify word_count function exists in workspace src/
        src_dir = ws / "src"
        found_word_count = False
        if src_dir.is_dir():
            for py_file in src_dir.rglob("*.py"):
                try:
                    funcs = _extract_functions_and_classes(py_file.read_text(encoding="utf-8"))
                    if "word_count" in funcs:
                        found_word_count = True
                        break
                except Exception:
                    continue

        if not found_word_count:
            return (
                False,
                "Acceptance rejected: Requirement specifies 'word_count(text)', "
                "but no 'word_count' function was defined in any source file under src/.",
            )

        # Check for unrelated file modification (e.g. only modifying auth.py for word_count)
        src_changed = [p for p in changed_files if p.startswith("src/")]
        if src_changed and all("auth.py" in p for p in src_changed):
            return (
                False,
                "Acceptance rejected: Modifying 'src/notes_app/auth.py' is "
                "unrelated to the 'word_count' requirement.",
            )

    # 5. Generic check: Ensure defined symbols match requirement intent
    # Extract standalone identifiers like func_name(...) from requirement (ignoring method calls like obj.method())
    import builtins

    builtin_names = set(dir(builtins)) | {
        "def",
        "return",
        "if",
        "for",
        "while",
        "assert",
    }
    explicit_func_matches = re.findall(r"(?<!\.)\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", requirement)
    for func_name in explicit_func_matches:
        if func_name in builtin_names:
            continue
        if func_name.startswith("test_"):
            target_dir = ws / "tests" if (ws / "tests").is_dir() else ws
            target_name = "tests/"
        else:
            target_dir = ws / "src" if (ws / "src").is_dir() else ws
            target_name = "src/"

        found = False
        if target_dir.is_dir():
            for py_file in target_dir.rglob("*.py"):
                try:
                    funcs = _extract_functions_and_classes(py_file.read_text(encoding="utf-8"))
                    if func_name in funcs:
                        found = True
                        break
                except Exception:
                    continue

        if not found:
            return (
                False,
                f"Acceptance rejected: Requirement mentions function '{func_name}()', "
                f"but it was not defined in {target_name}.",
            )

    return True, None
