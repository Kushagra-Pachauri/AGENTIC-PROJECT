"""Authoritative DONE Gate evaluation for RepoAgent runs and promotions."""

from __future__ import annotations

from pathlib import Path

from repoagent.acceptance import verify_requirement_acceptance
from repoagent.changeset_applier import workspace_has_modifications
from repoagent.schemas import ReviewVerdict, RunState, RunStatus
from repoagent.workspace_utils import compute_workspace_hash

KNOWN_SYNTHETIC_TEST_STRINGS = {
    "12 passed in 0.08s",
}


def evaluate_done_gate(
    state: RunState,
    workspace: Path,
    is_mock_test: bool = False,
    skip_acceptance_check: bool = False,
) -> tuple[bool, str | None]:
    """Evaluate DONE Gate conditions for a RunState and workspace.

    Requirements:
    1. Run status is not NEEDS_HUMAN or BLOCKED.
    2. At least one TestReport exists and the latest passed deterministically.
    3. The latest TestReport has a valid non-empty workspace_hash and exit_code == 0.
    4. Latest test report contains zero failed tests.
    5. Workspace has uncommitted modifications compared to baseline commit.
    6. Workspace is not the pristine baseline 'demo_repo'.
    7. Code Reviewer explicitly approved (verdict == ReviewVerdict.APPROVE).
    8. Review report has authoritative provenance (rejects fallback stubs).
    9. Test report has authoritative provenance (rejects synthetic strings).
    10. Workspace hash parity: recorded hash matches current disk hash.
    11. Requirement acceptance verification passes (rejects placeholder stubs).
    """
    if state.status in (RunStatus.NEEDS_HUMAN, RunStatus.BLOCKED):
        return False, f"Run status is '{state.status.value}', not eligible for DONE gate"

    last_report = state.test_reports[-1] if state.test_reports else None
    if not last_report or not last_report.passed or last_report.exit_code != 0:
        return False, "Latest test report missing, failed, or non-zero exit code"
    if bool(last_report.failed_tests):
        return False, f"Latest test report contains failed tests: {last_report.failed_tests}"
    if not bool(last_report.workspace_hash):
        return False, "Latest test report missing deterministic workspace hash"

    ws = Path(workspace).resolve()
    if not ws.is_dir():
        return False, f"Workspace directory not found: {ws}"

    has_mods = (
        workspace_has_modifications(ws)
        if (ws / ".git").is_dir()
        else (workspace_has_modifications(ws) or is_mock_test)
    )
    if not has_mods:
        return False, "Workspace contains no applied modifications"

    demo_repo_resolved = Path("demo_repo").resolve()
    if ws == demo_repo_resolved:
        return False, "Workspace is pristine baseline demo_repo"

    reviewer_approved = bool(state.review and state.review.verdict == ReviewVerdict.APPROVE)
    if not reviewer_approved:
        return False, "Code reviewer verdict is not APPROVE"

    if not is_mock_test:
        # Check review provenance (strict allowlist)
        review_prov = getattr(state.review, "provenance", None) if state.review else None
        if review_prov != "authoritative_adk_reviewer":
            return (
                False,
                f"Code reviewer report provenance must be 'authoritative_adk_reviewer', "
                f"got '{review_prov}'",
            )
        if state.review and "fallback review approve" in state.review.summary.lower():
            return (
                False,
                "Code reviewer verdict is from a fallback offline stub, "
                "not an authoritative review",
            )

        # Check test execution provenance (strict allowlist)
        test_prov = getattr(last_report, "provenance", None)
        if test_prov != "authoritative_docker_sandbox":
            return (
                False,
                f"Test execution provenance must be 'authoritative_docker_sandbox', "
                f"got '{test_prov}'",
            )
        if (
            last_report.raw_output
            and last_report.raw_output.strip() in KNOWN_SYNTHETIC_TEST_STRINGS
        ):
            return (
                False,
                "Test report contains known synthetic mock output ('12 passed in 0.08s'); "
                "real execution required",
            )

        # Disk hash parity check
        current_hash = compute_workspace_hash(ws)
        if not current_hash:
            return False, "Failed to compute deterministic workspace hash from disk"
        if last_report.workspace_hash != current_hash:
            return (
                False,
                f"Workspace hash mismatch: recorded '{last_report.workspace_hash}' "
                f"does not match current disk hash '{current_hash}'. "
                "Workspace was modified after testing or evidence is stale.",
            )

        # Requirement acceptance verification
        if not skip_acceptance_check and state.requirement:
            accepted, acc_reason = verify_requirement_acceptance(
                state.requirement, ws, state.changes
            )
            if not accepted:
                return False, f"Requirement acceptance verification failed: {acc_reason}"

    return True, None
