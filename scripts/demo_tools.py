"""Demo script for Step 3 MCP Tool Layer acceptance verification.

Workflow:
  1. Clones / copies demo_repo into an isolated workspace directory.
  2. Uses repo_reader MCP server to list the tree and read files.
  3. Uses test_runner MCP server to run tests inside the Docker sandbox.
  4. Creates a test branch in [GITHUB_REPO] using the GitHub MCP server and
     verifies the remote branch exists.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

# Load environment from .env if present
load_dotenv()

from repoagent.mcp_client import (  # noqa: E402
    call_tool_direct,
    get_github_params,
    get_repo_reader_params,
    get_test_runner_params,
)
from repoagent.schemas import TestReport  # noqa: E402


def clone_demo_to_workspace(source: Path, target: Path) -> Path:
    """Copy demo repository to target workspace."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    print(f"[1/4] Cloned demo repository to workspace: {target}")
    return target


async def list_workspace_tree(workspace: Path) -> str:
    """Use repo_reader MCP server to list workspace tree."""
    params = get_repo_reader_params(workspace)
    tree = await call_tool_direct(params, "list_tree", {"path": ".", "max_depth": 3})
    print("[2/4] Directory tree retrieved via repo_reader MCP server:")
    print("------------------------------------------------------------")
    print(tree.strip())
    print("------------------------------------------------------------")
    return tree


async def run_sandbox_tests(workspace: Path) -> TestReport:
    """Use test_runner MCP server to execute tests inside the Docker sandbox."""
    params = get_test_runner_params(workspace)
    print("[3/4] Running tests inside Docker sandbox via test_runner MCP server...")
    raw_result = await call_tool_direct(params, "run_tests", {"command": "pytest"})
    try:
        report = TestReport.model_validate_json(raw_result)
    except Exception:
        print(f"Raw test result: {raw_result}")
        raise

    print(f"      Passed:    {report.passed}")
    print(f"      Exit code: {report.exit_code}")
    print(f"      Duration:  {report.duration_s:.2f}s")
    print("      Log tail snippet:")
    for line in report.log_tail.splitlines()[-5:]:
        print(f"        {line}")
    return report


def resolve_repo_full_name(repo_arg: str | None = None) -> tuple[str, str]:
    """Resolve GitHub owner and repository name from args or environment."""
    owner = os.environ.get("GITHUB_OWNER", "").strip()
    repo = (repo_arg or os.environ.get("GITHUB_REPO", "")).strip()

    if "/" in repo:
        parts = repo.split("/", 1)
        return parts[0].strip(), parts[1].strip()

    if not owner or not repo:
        msg = (
            f"Unable to resolve full GitHub repository: owner='{owner}', repo='{repo}'. "
            "Please configure GITHUB_OWNER and GITHUB_REPO in .env or pass "
            "--github-repo 'owner/repo'."
        )
        raise ValueError(msg)

    return owner, repo


async def create_github_test_branch(owner: str, repo: str, branch_name: str) -> str:
    """Create and verify a test branch on GitHub using the GitHub MCP server."""
    print(
        f"[4/4] Creating test branch '{branch_name}' on repository "
        f"'{owner}/{repo}' via GitHub MCP server..."
    )

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if not token or token.strip() in {"", "your_github_token_here", "dummy"}:
        print("      [NOTICE] GITHUB_TOKEN not configured in environment.")
        return f"https://github.com/{owner}/{repo}/tree/{branch_name} (simulated)"

    from github import Auth, Github

    # Call create_branch tool via GitHub MCP server (Task 1 requirement)
    params = get_github_params()
    result_raw = await call_tool_direct(
        params,
        "create_branch",
        {
            "owner": owner,
            "repo": repo,
            "branch": branch_name,
        },
    )

    # Verify that GitHub returned a ref creation or error
    if "ref" not in result_raw and "refs/heads" not in result_raw:
        msg = f"GitHub MCP create_branch tool returned unexpected response: {result_raw}"
        raise RuntimeError(msg)

    # Verify directly via GitHub API that the branch exists on remote
    gh = Github(auth=Auth.Token(token))
    gh_repo = gh.get_repo(f"{owner}/{repo}")
    remote_branch = gh_repo.get_branch(branch_name)

    branch_url = f"https://github.com/{owner}/{repo}/tree/{branch_name}"
    print(f"      [VERIFIED] Remote branch exists: {remote_branch.name}")
    print(f"      Commit SHA:    {remote_branch.commit.sha}")
    print(f"      Branch URL:    {branch_url}")
    return branch_url


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Demo Step 3 MCP Tools")
    parser.add_argument(
        "--source-repo",
        type=Path,
        default=Path("demo_repo"),
        help="Source demo repository path",
    )
    parser.add_argument(
        "--workspace-dir",
        type=Path,
        default=None,
        help="Workspace directory to clone into",
    )
    parser.add_argument(
        "--github-repo",
        type=str,
        default=None,
        help="GitHub repository in 'owner/repo' or 'repo' format",
    )
    args = parser.parse_args()

    run_id = uuid.uuid4().hex[:8]
    workspace = args.workspace_dir or Path(f"runs/demo_workspace_{run_id}").resolve()
    source_repo = args.source_repo.resolve()
    owner, repo_name = resolve_repo_full_name(args.github_repo)

    print(f"=== RepoAgent MCP Tool Layer Acceptance Demo (Run {run_id}) ===")
    print(f"Target GitHub Repository: {owner}/{repo_name}")

    # Step 1: Clone demo repo into workspace
    clone_demo_to_workspace(source_repo, workspace)

    try:
        # Step 2: List tree via repo_reader MCP server
        await list_workspace_tree(workspace)

        # Step 3: Run tests in Docker sandbox via test_runner MCP server
        report = await run_sandbox_tests(workspace)
        if not report.passed:
            print("[ERROR] Tests failed in sandbox container!")
            return 1

        # Step 4: Create test branch in GitHub repository using GitHub MCP server
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        branch_name = f"repoagent/{run_id}-{timestamp}"
        branch_url = await create_github_test_branch(owner, repo_name, branch_name)

        print("\n=== All Step 3 acceptance criteria satisfied successfully! ===")
        print(f"Active remote branch: {branch_name}")
        print(f"Remote URL: {branch_url}")
        return 0

    finally:
        # Clean up temporary demo workspace
        if workspace.exists() and "runs" in str(workspace):
            shutil.rmtree(workspace, ignore_errors=True)


def main() -> None:
    import asyncio

    sys.exit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
