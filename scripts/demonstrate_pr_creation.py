"""Actual GitHub PR Demonstration Script for Run 0f04e14907d7.

Verifies DONE gate, checks PR #6 invariant, creates a dedicated feature branch,
pushes the two verified files, opens the GitHub PR, and verifies all details.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import dotenv
dotenv.load_dotenv()

from repoagent.done_gate import evaluate_done_gate
from repoagent.pr_agent import open_and_verify_pull_request
from repoagent.schemas import RunState, RunStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("demonstrate_pr")


def check_pr_6_status() -> dict[str, Any]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    owner = os.environ.get("GITHUB_OWNER", "Kushagra-Pachauri")
    repo = os.environ.get("GITHUB_REPO", "demo_repo")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/6",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "RepoAgent",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return {
            "state": data.get("state"),
            "merged": data.get("merged"),
            "number": data.get("number"),
            "title": data.get("title"),
        }


def main() -> None:
    run_id = "0f04e14907d7"
    run_dir = Path("runs") / run_id
    state_file = run_dir / "run_state.json"
    ws = run_dir / "workspace"

    logger.info("=== STEP 1: Verifying Run State and Invariants ===")
    state = RunState.model_validate_json(state_file.read_text(encoding="utf-8"))
    logger.info("Loaded state for run %s. Status: %s", run_id, state.status)

    # 1. Verify PR #6 is open and unmerged
    pr6_info = check_pr_6_status()
    logger.info("PR #6 Status: state=%s, merged=%s", pr6_info["state"], pr6_info["merged"])
    if pr6_info["state"] != "open" or pr6_info["merged"] is not False:
        logger.error("PR #6 invariant violated! Aborting.")
        sys.exit(1)

    # 2. Verify DONE Gate
    passed, reason = evaluate_done_gate(state, ws, is_mock_test=False)
    logger.info("Authoritative DONE Gate: passed=%s, reason=%s", passed, reason)
    if not passed:
        logger.error("Authoritative DONE Gate not satisfied: %s. Aborting.", reason)
        sys.exit(1)

    # === STEP 2: Open and Verify Pull Request ===
    logger.info("=== STEP 2: Executing open_and_verify_pull_request ===")
    pr_summary = open_and_verify_pull_request(
        state=state,
        workspace=ws,
        base_branch="main",
        is_live_execution=True,
    )

    logger.info("Successfully created and verified GitHub PR!")
    logger.info("PR URL: %s", pr_summary.url)
    logger.info("PR Number: #%s", pr_summary.pull_number)
    logger.info("PR Branch: %s", pr_summary.branch)
    logger.info("PR Title: %s", pr_summary.title)

    # === STEP 3: Update State and Reports ===
    state.pr = pr_summary
    state_file.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "report.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")

    # Re-read and verify PR #6 is STILL open and unmerged
    pr6_after = check_pr_6_status()
    logger.info("Post-creation check: PR #6 state=%s, merged=%s", pr6_after["state"], pr6_after["merged"])
    if pr6_after["state"] != "open" or pr6_after["merged"] is not False:
        logger.error("FATAL: PR #6 was modified! Expected OPEN.")
        sys.exit(1)

    # Query full PR details from GitHub API to report commit SHA and changed files
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    owner = os.environ.get("GITHUB_OWNER", "Kushagra-Pachauri")
    repo = os.environ.get("GITHUB_REPO", "demo_repo")
    pr_num = pr_summary.pull_number

    req_pr = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "RepoAgent"},
    )
    with urllib.request.urlopen(req_pr) as r:
        pr_details = json.loads(r.read().decode("utf-8"))

    req_files = urllib.request.Request(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}/files",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "RepoAgent"},
    )
    with urllib.request.urlopen(req_files) as r:
        pr_files = json.loads(r.read().decode("utf-8"))

    commit_sha = pr_details.get("head", {}).get("sha")
    changed_filenames = [f["filename"] for f in pr_files]

    logger.info("Commit SHA: %s", commit_sha)
    logger.info("Changed files on GitHub: %s", changed_filenames)

    # Append PR section to report.md
    report_md = run_dir / "report.md"
    current_md = report_md.read_text(encoding="utf-8")
    pr_section = [
        "",
        "## GitHub Pull Request Demonstration",
        f"- **PR URL**: {pr_summary.url}",
        f"- **PR Number**: #{pr_num}",
        f"- **Target Branch**: `main`",
        f"- **Source Branch**: `{pr_summary.branch}`",
        f"- **Commit SHA**: `{commit_sha}`",
        f"- **PR State**: `{pr_details.get('state')}`",
        f"- **Files Changed**:",
    ]
    for fn in changed_filenames:
        pr_section.append(f"  - `{fn}`")
    pr_section.append(f"- **PR #6 Status**: `OPEN` and `UNMERGED` (Verified)")

    report_md.write_text(current_md + "\n".join(pr_section) + "\n", encoding="utf-8")
    logger.info("Updated report.md with full PR demonstration details.")


if __name__ == "__main__":
    main()
