"""GitHub service for committing, pushing branches, and creating verified Pull Requests."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from github import Github

from repoagent.schemas import PRSummary

logger = logging.getLogger(__name__)


def push_branch_and_create_pr(
    workspace: Path,
    branch: str,
    title: str,
    body: str,
    base_branch: str = "main",
) -> PRSummary:
    """Commit workspace changes, push to GitHub on a run-specific branch, and create a real PR.

    Verifies the branch and Pull Request exist on GitHub before returning.
    Raises an error if GITHUB_TOKEN is missing or if the remote operation fails.
    Never returns a placeholder URL.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    owner = os.environ.get("GITHUB_OWNER")
    repo_name = os.environ.get("GITHUB_REPO")

    if not token or not owner or not repo_name:
        missing = [
            var
            for var, val in [
                ("GITHUB_TOKEN", token),
                ("GITHUB_OWNER", owner),
                ("GITHUB_REPO", repo_name),
            ]
            if not val
        ]
        msg = (
            "Cannot create GitHub Pull Request: missing required environment variables: "
            f"{', '.join(missing)}"
        )
        raise ValueError(msg)

    # Step 1: Ensure workspace is a git repo
    workspace_str = str(workspace.resolve())
    status_proc = subprocess.run(
        ["git", "status"],
        cwd=workspace_str,
        capture_output=True,
        text=True,
        check=False,
    )
    if status_proc.returncode != 0:
        subprocess.run(["git", "init"], cwd=workspace_str, check=True, capture_output=True)

    # Configure committer identity
    subprocess.run(
        ["git", "config", "user.name", "RepoAgent"],
        cwd=workspace_str,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "repoagent@example.com"],
        cwd=workspace_str,
        check=True,
        capture_output=True,
    )

    # Step 2: Switch to / create target branch
    subprocess.run(
        ["git", "checkout", "-B", branch],
        cwd=workspace_str,
        check=True,
        capture_output=True,
    )

    # Step 3: Stage and commit any unstaged/uncommitted files
    subprocess.run(["git", "add", "."], cwd=workspace_str, check=True, capture_output=True)
    commit_res = subprocess.run(
        ["git", "commit", "-m", title],
        cwd=workspace_str,
        capture_output=True,
        text=True,
        check=False,
    )
    # Return code 0 = committed, 1 = nothing to commit (which is fine if already committed)
    if commit_res.returncode not in (0, 1):
        msg = f"Failed to commit workspace changes: {commit_res.stderr}"
        raise RuntimeError(msg)

    # Step 4: Configure authenticated remote URL and push branch
    remote_url = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
    # Update or add origin remote
    remotes_res = subprocess.run(
        ["git", "remote"], cwd=workspace_str, capture_output=True, text=True, check=True
    )
    if "origin" in remotes_res.stdout.split():
        subprocess.run(
            ["git", "remote", "set-url", "origin", remote_url],
            cwd=workspace_str,
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=workspace_str,
            check=True,
            capture_output=True,
        )

    print(f"[*] Pushing branch '{branch}' to remote '{owner}/{repo_name}'...")
    push_res = subprocess.run(
        ["git", "push", "-u", "origin", branch, "--force"],
        cwd=workspace_str,
        capture_output=True,
        text=True,
        check=False,
    )
    if push_res.returncode != 0:
        # Avoid leaking token in exception message
        clean_err = push_res.stderr.replace(token, "[REDACTED]")
        msg = f"Failed to push branch '{branch}' to GitHub: {clean_err}"
        raise RuntimeError(msg)

    # Step 5: Verify branch on GitHub and create Pull Request via PyGithub
    print(f"[*] Verifying branch and creating Pull Request on '{owner}/{repo_name}'...")
    gh = Github(token)
    repo = gh.get_repo(f"{owner}/{repo_name}")

    # Verify branch exists remotely
    try:
        remote_branch_ref = repo.get_branch(branch)
        sha_prefix = remote_branch_ref.commit.sha[:8]
        print(f"[*] Verified remote branch '{remote_branch_ref.name}' (SHA: {sha_prefix})")
    except Exception as e:
        msg = f"Remote branch '{branch}' was not found on GitHub after push: {e}"
        raise RuntimeError(msg) from e

    # Determine base branch
    target_base = base_branch or repo.default_branch or "main"

    # Check if a PR already exists for this head branch
    existing_pulls = list(repo.get_pulls(state="open", head=f"{owner}:{branch}"))
    if existing_pulls:
        pr = existing_pulls[0]
        print(f"[*] Found existing open PR #{pr.number}: {pr.html_url}")
    else:
        pr = repo.create_pull(
            title=title,
            body=body,
            head=branch,
            base=target_base,
        )
        print(f"[*] Created new Pull Request #{pr.number}: {pr.html_url}")

    # Step 6: Verify PR through GitHub API
    if not pr.html_url or "github.com" not in pr.html_url:
        msg = f"GitHub API returned invalid PR URL: {pr.html_url}"
        raise RuntimeError(msg)

    return PRSummary(
        title=pr.title,
        body_markdown=body,
        url=pr.html_url,
        branch=branch,
    )
