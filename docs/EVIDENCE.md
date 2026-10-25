# RepoAgent Step 5 — Evidence Run Records

This document preserves the durable audit record of the three primary Step 5 runs: the historical invalid run, the live local model run under correctness gating, and the verified passing replay run.

---

## Run 1: Historical Invalid Run (`97e2a052c992`)

- **Run ID**: `97e2a052c992`
- **Date**: 2026-10-24
- **Reported Status**: `done` (SUPERSEDED / INVALID)
- **True Status**: **INVALID (False Positive)**
- **Audit Findings**:
  - The orchestrator contained a condition that bypassed applying generated diffs when targeting `demo_repo`.
  - The deterministic test runner executed against pristine `demo_repo`, running only the 12 baseline tests (12/12 passed).
  - The Code Reviewer was presented with baseline passing test output and approved the unapplied patch.
  - No password reset changes were actually compiled or verified on disk.
- **Remediation**:
  - Superseded by isolated workspace quarantine under `runs/<run_id>/workspace` and strict invariant gating in `flow.py`.
  - Durable invalidation record committed at [`runs/97e2a052c992/INVALID.txt`](../runs/97e2a052c992/INVALID.txt).

---

## Run 2: Controlled Live Model Run (`7bb1fb173e81`)

- **Run ID**: `7bb1fb173e81`
- **Flow ID**: `011cb371-70b1-45ab-838d-7d9352ce8b67`
- **Date**: 2026-10-25T11:03:43Z
- **Model**: Local Ollama `qwen2.5-coder:3b` (via `openai_compatible` provider)
- **Requirement**: `Add a password-reset functionality`
- **Final Status**: `needs_human` (Exit Code: 1)
- **Retries Used**: 3 / 3
- **Reviewer Invoked**: No (`review: null`)
- **PR Opened**: No (`pr: null`)
- **Audit Findings**:
  - **Workspace Isolation**: Generated changes applied exclusively to `runs/7bb1fb173e81/workspace` (`src/notes_app/app.py`, `src/notes_app/auth.py`). Baseline `demo_repo` remained 100% pristine.
  - **Compilation**: Applied Python files compiled cleanly via AST / `py_compile`.
  - **Gating Enforcement**: The model generated implementation methods but omitted test files in `tests/`. Correctness gating deterministically flagged `verification:missing_feature_tests` on each iteration.
  - **Invariant Preservation**: Because tests never passed, the Reviewer was never called, and the flow safely terminated with `status=needs_human` after exhausting retries.

---

## Run 3: Verified Reference Replay Run (`replay_4c73dac5`)

- **Run ID**: `replay_4c73dac5`
- **Flow ID**: `253cf6da-bf3e-42cf-aa4a-66f85b0cb34e`
- **Date**: 2026-10-25T11:46:21Z
- **Replay Mode**: `true`
- **Replay Label**: `hand-written reference solution, not model output`
- **Reference Fixture**: `tests/fixtures/replay_password_reset.json`
- **Final Status**: `done` (Exit Code: 0)
- **Retries Used**: 0 / 3
- **Audit Findings**:
  - **Change Application**: Applied hand-written reference solution through the standard ChangeSet applier to `runs/replay_4c73dac5/workspace` (`auth.py`, `app.py`, `test_auth.py`).
  - **Deterministic Test Verification**: Executed `pytest` inside the Docker sandbox container. Collected and passed **15 items** (12 baseline + 3 new test functions) in 0.37s.
  - **New Passed Test Nodes**:
    - `tests/test_auth.py::TestAuth::test_request_password_reset_success`
    - `tests/test_auth.py::TestAuth::test_reset_password_with_valid_token`
    - `tests/test_auth.py::TestAuth::test_reset_password_with_invalid_token`
  - **Workspace Hash Integrity**: Tested workspace hash (`26eb01594b153a3e7e70bb817be459f6f0afa50280fae6ae1dbd3b254a09c91d`) differed from baseline hash (`fabe79b6a1f86c3fe020ed378d7769302a8110b16ff0dfa150c5526b0c226718`).
  - **A2A Agent Ordering**:
    - Planner called over A2A prior to coding (`11:47:45Z`).
    - Reviewer called over A2A only after deterministic tests passed (`11:48:23Z`).
  - **Reviewer Verdict**: `approve` with 0 findings.
  - **PR Summary**: Produced valid PR summary for branch `repoagent/replay_4c73dac5`.
