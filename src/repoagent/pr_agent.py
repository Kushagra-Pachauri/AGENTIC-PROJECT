"""PR Agent and Report Writer — GitHub MCP integration and deterministic PR generation.

Step 6 deliverables:
  - PRAgent with deterministic baseline preflight, branch creation, atomic file push,
    and Pull Request opening via GitHub MCP server.
  - Deterministic PR content generation from RunState (no LLM hallucinations).
  - Post-creation verification (reading back PR details and changed-file list).
  - Report writer generating runs/<run_id>/report.json and runs/<run_id>/report.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from repoagent.done_gate import evaluate_done_gate
from repoagent.mcp_client import call_tool_direct, get_github_params
from repoagent.schemas import PRSummary, RunState, RunStatus
from repoagent.workspace_utils import get_runs_dir

logger = logging.getLogger(__name__)


def resolve_owner_repo(repo_url: str | None = None) -> tuple[str, str]:
    """Resolve GitHub owner and repository name from environment or repo_url."""
    owner = os.environ.get("GITHUB_OWNER", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()

    if repo and "/" in repo:
        parts = repo.split("/", 1)
        return parts[0].strip(), parts[1].strip()

    if owner and repo:
        return owner, repo

    if repo_url:
        cleaned = repo_url.rstrip("/").removesuffix(".git")
        if "github.com" in cleaned:
            parts = cleaned.split("github.com/")[-1].split("/")
            if len(parts) >= 2:
                return parts[0].strip(), parts[1].strip()
        elif "/" in cleaned:
            parts = cleaned.split("/", 1)
            return parts[0].strip(), parts[1].strip()

    # Default fallback
    return "Kushagra-Pachauri", "demo_repo"


def normalize_newlines(text: str) -> str:
    """Normalize all carriage returns to standard LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def detect_line_ending(text: str) -> str:
    """Detect whether text predominantly uses CRLF or LF."""
    return "\r\n" if "\r\n" in text else "\n"


def get_workspace_disk_diff_files(workspace: Path) -> dict[str, str]:
    """Return all modified and untracked files relative to the baseline commit in workspace.

    Excludes forbidden files: .env, runs/, __pycache__, *.pyc, *.git.
    """
    ws = Path(workspace).resolve()
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(ws),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    changed: dict[str, str] = {}
    forbidden_prefixes = (".env", "runs", "__pycache__", ".git")
    forbidden_suffixes = (".pyc", ".pyo")

    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        entry_path = line[3:].strip()
        if " -> " in entry_path:
            entry_path = entry_path.split(" -> ")[1].strip()
        entry_path = entry_path.strip('"').replace("\\", "/")

        parts = entry_path.split("/")
        if any(p.startswith(forbidden_prefixes) for p in parts):
            continue
        if entry_path.endswith(forbidden_suffixes):
            continue

        file_on_disk = ws / entry_path
        if file_on_disk.is_file():
            changed[entry_path] = file_on_disk.read_text(encoding="utf-8")

    return changed


def build_deterministic_pr_content(
    state: RunState,
    changed_files: list[str] | None = None,
) -> tuple[str, str]:
    """Build deterministic PR title and body from RunState and disk-derived changed files."""
    if changed_files is None:
        changed_files = [f.path for f in state.changes.files] if state.changes else []
    is_replay = bool(state.is_replay)
    prefix = "[REPLAY] " if is_replay else ""
    req_summary = state.requirement.strip().splitlines()[0] if state.requirement else "Implement feature"
    title = f"{prefix}feat: {req_summary}".strip()
    if len(title) > 250:
        title = title[:247] + "..."

    body_lines: list[str] = []
    if is_replay:
        body_lines.append("> **Note**: Hand-written reference solution, not model output.\n")

    body_lines.append(f"## Requirement\n{state.requirement}\n")

    plan_summary = state.plan.summary if state.plan else "N/A"
    body_lines.append(f"## Plan Summary\n{plan_summary}\n")

    body_lines.append("## Files Changed")
    for f in sorted(changed_files):
        body_lines.append(f"- `{f}`")
    body_lines.append("")

    body_lines.append("## Test Verification")
    body_lines.append("| Command | Passed | Exit Code | Workspace Hash | Duration |")
    body_lines.append("|---------|--------|-----------|----------------|----------|")
    new_tests_passed: list[str] = []
    for report in state.test_reports:
        passed_icon = "PASS" if report.passed else "FAIL"
        ws_hash = report.workspace_hash[:16] + "..." if report.workspace_hash else "None"
        cmd = report.command
        code = report.exit_code
        dur = report.duration_s
        body_lines.append(f"| `{cmd}` | {passed_icon} | {code} | `{ws_hash}` | {dur:.2f}s |")
        if report.passed and report.log_tail:
            for line in report.log_tail.splitlines():
                if "::" in line and ("PASSED" in line or "passed" in line):
                    test_name = line.split()[0]
                    if test_name not in new_tests_passed and "test_" in test_name:
                        new_tests_passed.append(test_name)

    if new_tests_passed:
        body_lines.append("\n### New Tests Passed")
        for t in new_tests_passed:
            body_lines.append(f"- `{t}`")
    body_lines.append("")

    body_lines.append("## Code Review")
    if state.review:
        body_lines.append(f"- **Verdict**: `{state.review.verdict.value}`")
        body_lines.append(f"- **Summary**: {state.review.summary}")
        if state.review.findings:
            body_lines.append("- **Findings**:")
            for f in state.review.findings:
                body_lines.append(f"  - [{f.severity}] `{f.file}:{f.line}`: {f.message}")
        else:
            body_lines.append("- **Findings**: None (Clean)")
    else:
        body_lines.append("No review report available.")
    body_lines.append("")

    body_lines.append("## Execution Metadata")
    body_lines.append(f"- **Run ID**: `{state.run_id}`")
    body_lines.append(f"- **Correction Iterations**: {state.retry_count} / {state.max_retries}")
    body_lines.append("")
    body_lines.append("Created by RepoAgent. A human must review before merging.")

    return title, "\n".join(body_lines)


async def _async_check_remote_baseline(
    params: Any,
    owner: str,
    repo: str,
    base_branch: str,
    workspace: Path,
    changed_files: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Verify that existing modified files match the remote baseline on main.

    Returns:
        tuple of (remote_path_mapping, line_endings_mapping).

    Raises:
        ValueError if a remote file differs from the workspace's baseline commit.
    """
    ws = Path(workspace).resolve()
    remote_paths: dict[str, str] = {}
    line_endings: dict[str, str] = {}

    for rel_path in changed_files:
        # Determine candidate remote paths: direct or with demo_repo/ prefix
        candidate_paths = [rel_path, f"demo_repo/{rel_path}"]
        remote_content: str | None = None
        matched_remote_path: str = rel_path

        for cand in candidate_paths:
            try:
                res_raw = await call_tool_direct(
                    params,
                    "get_file_contents",
                    {
                        "owner": owner,
                        "repo": repo,
                        "path": cand,
                        "branch": base_branch,
                    },
                )
                data = json.loads(res_raw)
                if isinstance(data, dict) and "content" in data:
                    remote_content = data["content"]
                    matched_remote_path = cand
                    break
            except Exception:
                continue

        remote_paths[rel_path] = matched_remote_path

        if remote_content is not None:
            # File already exists on remote main: compare with workspace's baseline commit
            base_proc = subprocess.run(
                ["git", "show", f"HEAD:{rel_path}"],
                cwd=str(ws),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if base_proc.returncode == 0:
                baseline_content = base_proc.stdout
                norm_remote = normalize_newlines(remote_content)
                norm_baseline = normalize_newlines(baseline_content)
                if norm_remote != norm_baseline:
                    msg = (
                        f"Baseline preflight failed: remote file '{matched_remote_path}' "
                        f"on '{base_branch}' differs from workspace baseline version."
                    )
                    raise ValueError(msg)

            # Record remote line ending
            line_endings[rel_path] = detect_line_ending(remote_content)
        else:
            line_endings[rel_path] = "\n"

    return remote_paths, line_endings


async def _async_open_and_verify_pr(
    state: RunState,
    workspace: Path,
    base_branch: str = "main",
    is_live_execution: bool = True,
) -> PRSummary:
    """Execute the full PR creation and post-verification lifecycle over GitHub MCP."""
    if is_live_execution is False:
        raise ValueError(
            "Cannot open PR: Offline or fallback execution cannot authorize real PR promotion."
        )

    if state.status != RunStatus.DONE:
        raise ValueError(
            f"Cannot open PR: Run status is '{state.status.value}'. "
            "PR creation is forbidden for runs not in DONE status."
        )

    review_prov = getattr(state.review, "provenance", None) if state.review else None
    if review_prov != "authoritative_adk_reviewer":
        raise ValueError(
            f"Cannot open PR: Review provenance is not authoritative ('{review_prov}')."
        )
    if state.review and "fallback" in state.review.summary.lower():
        raise ValueError("Cannot open PR: Review verdict is from a fallback offline stub.")

    if not state.test_reports:
        raise ValueError("Cannot open PR: Missing test reports.")

    last_rep = state.test_reports[-1]
    test_prov = getattr(last_rep, "provenance", None)
    if test_prov != "authoritative_docker_sandbox":
        raise ValueError(
            f"Cannot open PR: Test execution provenance is not authoritative ('{test_prov}')."
        )
    if last_rep.raw_output and last_rep.raw_output.strip() == "12 passed in 0.08s":
        raise ValueError("Cannot open PR: Test execution contains synthetic mock output.")

    gate_passed, reason = evaluate_done_gate(state, workspace, is_mock_test=False)
    if not gate_passed:
        raise ValueError(f"Cannot open PR: DONE gate conditions not satisfied: {reason}")

    if state.pr and state.pr.url and state.pr.pull_number and "github.com" in state.pr.url:
        logger.info(
            "PR already exists for run '%s' at %s (#%s)",
            state.run_id,
            state.pr.url,
            state.pr.pull_number,
        )
        return state.pr

    owner, repo = resolve_owner_repo(state.repo_url)
    ws = Path(workspace).resolve()
    changed_files = get_workspace_disk_diff_files(ws)

    if not changed_files:
        msg = "Cannot open PR: No modified files found in workspace disk diff."
        raise ValueError(msg)

    params = get_github_params()

    # Step 1: Baseline Preflight check
    remote_paths, line_endings = await _async_check_remote_baseline(
        params, owner, repo, base_branch, ws, changed_files
    )

    # Step 2: Create target branch from main
    branch_name = f"repoagent/{state.run_id}"
    try:
        await call_tool_direct(
            params,
            "create_branch",
            {
                "owner": owner,
                "repo": repo,
                "branch": branch_name,
                "from_branch": base_branch,
            },
        )
        logger.info("Created remote branch '%s' on %s/%s", branch_name, owner, repo)
    except Exception as exc:
        logger.info("Branch '%s' already exists or creation skipped: %s", branch_name, exc)

    # Step 3: Push changed files in a single atomic commit
    files_payload: list[dict[str, str]] = []
    for rel_path, local_content in changed_files.items():
        rem_path = remote_paths[rel_path]
        ending = line_endings.get(rel_path, "\n")
        formatted = normalize_newlines(local_content).replace("\n", ending)
        files_payload.append({"path": rem_path, "content": formatted})

    commit_msg = f"feat: {state.requirement}"
    await call_tool_direct(
        params,
        "push_files",
        {
            "owner": owner,
            "repo": repo,
            "branch": branch_name,
            "files": files_payload,
            "message": commit_msg,
        },
    )
    logger.info("Pushed %d files to '%s'", len(files_payload), branch_name)

    # Step 4: Open Pull Request
    title, body = build_deterministic_pr_content(state, list(changed_files.keys()))
    pr_raw = await call_tool_direct(
        params,
        "create_pull_request",
        {
            "owner": owner,
            "repo": repo,
            "title": title,
            "body": body,
            "head": branch_name,
            "base": base_branch,
        },
    )
    pr_data = json.loads(pr_raw) if isinstance(pr_raw, str) else pr_raw
    pull_number = int(pr_data.get("number") or pr_data.get("id"))
    pr_url = pr_data.get("html_url") or f"https://github.com/{owner}/{repo}/pull/{pull_number}"
    logger.info("Opened PR #%d at %s", pull_number, pr_url)

    # Step 5: Post-creation verification
    pr_info_raw = await call_tool_direct(
        params,
        "get_pull_request",
        {
            "owner": owner,
            "repo": repo,
            "pull_number": pull_number,
        },
    )
    pr_info = json.loads(pr_info_raw) if isinstance(pr_info_raw, str) else pr_info_raw

    if pr_info.get("state") != "open":
        msg = (
            f"PR verification failed: PR #{pull_number} is not open (state: {pr_info.get('state')})"
        )
        raise RuntimeError(msg)

    base_ref = pr_info.get("base", {}).get("ref")
    if base_ref != base_branch:
        msg = f"PR verification failed: PR base branch is '{base_ref}', expected '{base_branch}'"
        raise RuntimeError(msg)

    head_ref = pr_info.get("head", {}).get("ref")
    if head_ref != branch_name:
        msg = f"PR verification failed: PR head branch is '{head_ref}', expected '{branch_name}'"
        raise RuntimeError(msg)

    # Plausible changes check & file list verification with retry
    files_list: list[dict[str, Any]] = []
    for _ in range(3):
        files_list_raw = await call_tool_direct(
            params,
            "get_pull_request_files",
            {
                "owner": owner,
                "repo": repo,
                "pull_number": pull_number,
            },
        )
        files_list = (
            json.loads(files_list_raw) if isinstance(files_list_raw, str) else files_list_raw
        )
        if files_list:
            break
        await asyncio.sleep(1.0)

    pr_file_names = [f["filename"] for f in files_list]
    # Normalize both file lists to compare relative paths
    norm_pr_files = sorted([f.removeprefix("demo_repo/") for f in pr_file_names])
    norm_disk_files = sorted([f.removeprefix("demo_repo/") for f in changed_files])

    if norm_pr_files != norm_disk_files:
        msg = (
            f"PR verification failed: PR files {norm_pr_files} do not match "
            f"workspace disk diff files {norm_disk_files}."
        )
        raise RuntimeError(msg)

    # Plausible changes check: each file should have additions > 0
    for f in files_list:
        additions = f.get("additions", 0)
        changes = f.get("changes", 0)
        if additions <= 0 and changes <= 0:
            msg = f"PR verification failed: file '{f.get('filename')}' has 0 additions/changes."
            raise RuntimeError(msg)

    return PRSummary(
        title=title,
        body_markdown=body,
        url=pr_url,
        branch=branch_name,
        pull_number=pull_number,
    )


def open_and_verify_pull_request(
    state: RunState,
    workspace: Path,
    base_branch: str = "main",
    is_live_execution: bool = True,
) -> PRSummary:
    """Synchronous entry point for PR creation and verification, supporting nested loops."""
    if is_live_execution is False:
        raise ValueError(
            "Cannot open PR: Offline or fallback execution cannot authorize real PR promotion."
        )

    # 1. Authoritative gate check: NEVER allow PR creation if run is in
    # needs_human, blocked, or failed
    if state.status != RunStatus.DONE:
        raise ValueError(
            f"Cannot open PR: Run status is '{state.status.value}'. "
            "PR creation is forbidden for runs not in DONE status."
        )

    review_prov = getattr(state.review, "provenance", None) if state.review else None
    if review_prov != "authoritative_adk_reviewer":
        raise ValueError(
            f"Cannot open PR: Review provenance is not authoritative ('{review_prov}')."
        )
    if state.review and "fallback" in state.review.summary.lower():
        raise ValueError("Cannot open PR: Review verdict is from a fallback offline stub.")

    if not state.test_reports:
        raise ValueError("Cannot open PR: Missing test reports.")

    last_rep = state.test_reports[-1]
    test_prov = getattr(last_rep, "provenance", None)
    if test_prov != "authoritative_docker_sandbox":
        raise ValueError(
            f"Cannot open PR: Test execution provenance is not authoritative ('{test_prov}')."
        )
    if last_rep.raw_output and last_rep.raw_output.strip() == "12 passed in 0.08s":
        raise ValueError("Cannot open PR: Test execution contains synthetic mock output.")

    gate_passed, reason = evaluate_done_gate(state, workspace, is_mock_test=False)
    if not gate_passed:
        raise ValueError(f"Cannot open PR: DONE gate conditions not satisfied: {reason}")

    if state.pr and state.pr.url and state.pr.pull_number and "github.com" in state.pr.url:
        logger.info(
            "PR already exists for run '%s' at %s (#%s)",
            state.run_id,
            state.pr.url,
            state.pr.pull_number,
        )
        return state.pr

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(
                asyncio.run,
                _async_open_and_verify_pr(
                    state, workspace, base_branch, is_live_execution=is_live_execution
                ),
            )
            return future.result()
    return asyncio.run(
        _async_open_and_verify_pr(
            state, workspace, base_branch, is_live_execution=is_live_execution
        )
    )


def load_authoritative_run_state(run_dir: Path) -> RunState:
    """Load and verify consistent RunState from a run directory.

    Deterministic consistency policy:
    - Both report.json and run_state.json must exist in run_dir.
    - Both must parse cleanly into valid RunState models.
    - Core fields must match exactly: run_id, requirement, is_replay, status,
      test_reports count, and review verdict.
    - Any mismatch, corruption, or missing file raises ValueError or FileNotFoundError.
    """
    r_dir = Path(run_dir).resolve()
    report_file = r_dir / "report.json"
    checkpoint_file = r_dir / "run_state.json"

    if not report_file.is_file() and not checkpoint_file.is_file():
        raise FileNotFoundError(f"No run state or report found in {r_dir}")

    if not report_file.is_file() or not checkpoint_file.is_file():
        raise ValueError(
            f"State integrity check failed in {r_dir}: both report.json and run_state.json "
            "must exist for an authoritative completed run."
        )

    try:
        data_rep = json.loads(report_file.read_text(encoding="utf-8"))
        state_report = RunState.model_validate(data_rep)
    except Exception as exc:
        raise ValueError(f"Corrupted or invalid report.json in {r_dir}: {exc}") from exc

    try:
        data_chk = json.loads(checkpoint_file.read_text(encoding="utf-8"))
        state_checkpoint = RunState.model_validate(data_chk)
    except Exception as exc:
        raise ValueError(f"Corrupted or invalid run_state.json in {r_dir}: {exc}") from exc

    mismatches: list[str] = []
    if state_report.run_id != state_checkpoint.run_id:
        mismatches.append(f"run_id mismatch: {state_report.run_id} vs {state_checkpoint.run_id}")
    if state_report.requirement != state_checkpoint.requirement:
        mismatches.append(
            f"requirement mismatch: {state_report.requirement} vs {state_checkpoint.requirement}"
        )
    if state_report.is_replay != state_checkpoint.is_replay:
        mismatches.append(
            f"is_replay mismatch: {state_report.is_replay} vs {state_checkpoint.is_replay}"
        )
    if state_report.status != state_checkpoint.status:
        mismatches.append(f"status mismatch: {state_report.status} vs {state_checkpoint.status}")
    if len(state_report.test_reports) != len(state_checkpoint.test_reports):
        mismatches.append(
            f"test_reports count mismatch: {len(state_report.test_reports)} vs "
            f"{len(state_checkpoint.test_reports)}"
        )
    rep_verdict = state_report.review.verdict if state_report.review else None
    chk_verdict = state_checkpoint.review.verdict if state_checkpoint.review else None
    if rep_verdict != chk_verdict:
        mismatches.append(f"review verdict mismatch: {rep_verdict} vs {chk_verdict}")

    if mismatches:
        raise ValueError(
            f"State integrity check failed in {r_dir}: conflicting state files. "
            f"Mismatches: {'; '.join(mismatches)}"
        )

    return state_report


def promote_existing_run(
    run_id: str,
    runs_dir: Path | None = None,
    live_pr: bool = False,
) -> RunState:
    """Safely promote a completed successful run's workspace to a Pull Request.

    Enforces strict safety invariants:
    1. Run ID format and path traversal validation.
    2. Authoritative state consistency between report.json and run_state.json.
    3. Workspace containment within runs/<run_id>/workspace.
    4. Target repository linkage and baseline commit verification.
    5. Re-evaluates full DONE gate (reusing evaluate_done_gate).
    6. Rejects runs with status needs_human, failed, or running.
    7. PR idempotency: never duplicates an already existing PR.
    8. Strict push scoping: diff contains only allowed workspace feature files.
    """
    if not run_id or not re.match(r"^[A-Za-z0-9_-]+$", run_id):
        raise ValueError(
            f"Invalid run_id '{run_id}'. Must be alphanumeric with hyphens or underscores."
        )

    base_runs = (runs_dir or get_runs_dir()).resolve()
    run_dir = (base_runs / run_id).resolve()

    try:
        run_dir.relative_to(base_runs)
    except ValueError as exc:
        raise ValueError(f"Path traversal detected: '{run_id}' escapes runs directory.") from exc

    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    # 1. State integrity check (deterministic consistency policy)
    state = load_authoritative_run_state(run_dir)
    if state.run_id != run_id:
        raise ValueError(
            f"State run_id '{state.run_id}' does not match requested run_id '{run_id}'."
        )

    # 2. Status check: never promote needs_human or failed runs
    if state.status != RunStatus.DONE:
        raise ValueError(
            f"Cannot promote run '{run_id}': run status is '{state.status.value}'. "
            "Only runs that completed with status 'done' can be promoted to a PR."
        )

    # 3. Workspace containment and git baseline verification
    workspace = (run_dir / "workspace").resolve()
    try:
        workspace.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError(f"Workspace path escapes run directory: {workspace}") from exc

    if not workspace.is_dir() or not (workspace / ".git").is_dir():
        raise FileNotFoundError(f"Isolated workspace missing or not a git repository: {workspace}")

    log_proc = subprocess.run(
        ["git", "log", "--pretty=%B"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        check=False,
    )
    log_text = log_proc.stdout or ""
    if "baseline repo state" not in log_text and "baseline demo_repo state" not in log_text:
        raise ValueError(
            f"Workspace in {workspace} does not have the expected RepoAgent baseline commit."
        )

    # 4. Strict push scoping: only allowed feature files
    changed_files = get_workspace_disk_diff_files(workspace)
    if not changed_files:
        raise ValueError(f"Workspace in {workspace} contains no modified feature files.")

    for path in changed_files:
        if not (path.startswith("src/notes_app/") or path.startswith("tests/")):
            raise ValueError(f"Workspace contains changes outside allowed feature paths: '{path}'.")

    # 5. Full DONE Gate evaluation (reusing evaluate_done_gate from done_gate.py)
    from repoagent.done_gate import evaluate_done_gate

    gate_passed, reason = evaluate_done_gate(state, workspace, is_mock_test=False)
    if not gate_passed:
        raise ValueError(f"DONE gate validation failed for run '{run_id}': {reason}")

    # 6. PR Idempotency: never duplicate an existing PR
    if state.pr and state.pr.url and state.pr.pull_number and "github.com" in state.pr.url:
        logger.info(
            "PR already exists for run '%s' at %s (#%s)",
            run_id,
            state.pr.url,
            state.pr.pull_number,
        )
        pr_msg = f"[IDEMPOTENT] PR already exists for '{run_id}': {state.pr.url}"
        print(pr_msg)
        return state

    # 7. PR creation or offline verification
    if live_pr:
        pr_summary = open_and_verify_pull_request(state=state, workspace=workspace)
        state.pr = pr_summary
    else:
        title, body = build_deterministic_pr_content(state, list(changed_files.keys()))
        state.pr = PRSummary(
            title=title,
            body_markdown=body,
            url=f"https://github.com/offline-test/repo/pull/{state.run_id[:6]}",
            branch=state.branch or f"repoagent/{state.run_id}",
        )

    # 8. Update run reports with the new PR details
    write_run_reports(run_dir, state)
    (run_dir / "run_state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return state


def generate_report_markdown(state: RunState) -> str:
    """Generate comprehensive Markdown run report."""
    md: list[str] = []
    status_icon = "DONE" if state.status.value == "done" else state.status.value.upper()
    md.append(f"# RepoAgent Run Report: `{state.run_id}`\n")
    md.append(f"- **Final Status**: `{status_icon}`")
    md.append(f"- **Requirement**: {state.requirement}")
    md.append(f"- **Target Branch**: `{state.branch or 'N/A'}`")
    md.append(f"- **Retries Used**: {state.retry_count} / {state.max_retries}")
    if state.is_replay:
        md.append("- **Execution Mode**: `REPLAY` (hand-written reference solution)")
    else:
        md.append("- **Execution Mode**: `LIVE`")
    md.append("")

    # Plan
    md.append("## Plan")
    if state.plan:
        md.append(f"**Summary**: {state.plan.summary}\n")
        if state.plan.steps:
            md.append("**Planned Steps**:")
            for s in state.plan.steps:
                md.append(f"1. {s}")
    else:
        md.append("No plan recorded.")
    md.append("")

    # Changes
    md.append("## Changes Applied")
    if state.changes and state.changes.files:
        md.append(f"**Summary**: {state.changes.summary}\n")
        md.append("| File | Action |")
        md.append("|------|--------|")
        for f in state.changes.files:
            md.append(f"| `{f.path}` | `{f.action}` |")
    else:
        md.append("No changes applied.")
    md.append("")

    # Deterministic Tests
    md.append("## Test Reports")
    if state.test_reports:
        md.append("| Attempt | Command | Passed | Exit Code | Workspace Hash | Duration |")
        md.append("|---------|---------|--------|-----------|----------------|----------|")
        for idx, tr in enumerate(state.test_reports, 1):
            passed = "PASS" if tr.passed else "FAIL"
            h = tr.workspace_hash[:16] + "..." if tr.workspace_hash else "None"
            cmd = tr.command
            dur = tr.duration_s
            md.append(f"| {idx} | `{cmd}` | {passed} | {tr.exit_code} | `{h}` | {dur:.2f}s |")
    else:
        md.append("No test reports captured.")
    md.append("")

    # Code Review
    md.append("## Code Review")
    if state.review:
        md.append(f"- **Verdict**: `{state.review.verdict.value}`")
        md.append(f"- **Summary**: {state.review.summary}")
        if state.review.findings:
            md.append("\n**Findings**:")
            for fd in state.review.findings:
                md.append(f"- [{fd.severity}] `{fd.file}:{fd.line}`: {fd.message}")
    else:
        md.append("No code review executed.")
    md.append("")

    # Pull Request
    md.append("## Pull Request")
    if state.pr and state.pr.url:
        md.append(f"- **PR URL**: [{state.pr.url}]({state.pr.url})")
        if state.pr.pull_number:
            md.append(f"- **PR Number**: #{state.pr.pull_number}")
        md.append(f"- **Branch**: `{state.pr.branch}`")
        md.append(f"- **Title**: {state.pr.title}")
    else:
        md.append("No pull request was opened for this run.")
    md.append("")

    # Errors
    if state.errors:
        md.append("## Errors & Diagnostic Notes")
        for err in state.errors:
            md.append(f"- `{err}`")
        md.append("")

    return "\n".join(md)


def write_run_reports(run_dir: Path, state: RunState) -> None:
    """Write report.json and report.md in the designated run directory."""
    target_dir = Path(run_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. report.json: full RunState dump
    report_json_path = target_dir / "report.json"
    report_json_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")

    # 2. report.md: human-readable Markdown summary with tables
    report_md_path = target_dir / "report.md"
    report_md_path.write_text(generate_report_markdown(state), encoding="utf-8")
