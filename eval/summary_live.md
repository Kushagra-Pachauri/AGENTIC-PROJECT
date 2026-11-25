# RepoAgent Step 9: Evaluation Summary Report (LIVE Mode)

**Date:** 2026-11-20 13:42:35 UTC  
**Evaluation Mode:** `live`  
**Tasks Evaluated:** 15  
**Model / Engine:** `ollama:qwen2.5-coder:3b`  

---

## 1. High-Level Performance Metrics

| Metric | Observed Value | Percentage / Notes |
| :--- | :---: | :--- |
| **Total Tasks Evaluated** | `15` | 100% |
| **Overall Task Success (DONE + Acceptance Passed)** | `0` | **0.0%** |
| **Independent Acceptance Test Pass Rate** | `0` | 0.0% |
| **Existing Regression Test Pass Rate** | `15` | 100.0% |
| **Human Intervention Required (`NEEDS_HUMAN`)** | `14` | 93.3% |
| **Guardrail Rejections (`BLOCKED`)** | `0` | 0.0% |
| **Average Execution Time** | `34.17s` | Per task |
| **Monetary API Cost** | `$0.00` | Local Ollama infrastructure |
| **Token Usage** | `N/A` | Honest accounting (engine unexposed) |
| **PR Creation Safety** | `0 created` | Promotion gated in eval mode |

---

## 2. Difficulty Tier Breakdown

| Difficulty Tier | Total Tasks | Successful | Success Rate |
| :--- | :---: | :---: | :---: |
| **Easy** | 5 | 0 | 0.0% |
| **Medium** | 5 | 0 | 0.0% |
| **Hard** | 5 | 0 | 0.0% |

---

## 3. Extrapolations for 100 Runs (Observed Baseline)

> [!NOTE]
> These figures are linear mathematical extrapolations based strictly on measured sample data, not guaranteed outcomes.

- **Estimated Wall-Clock Time:** ~0.95 hours (3417 seconds)
- **Estimated Monetary Cost:** **$0.00** (Local Ollama inference)
- **Expected Successful Runs:** ~0.0 / 100
- **Expected Human Interventions (`NEEDS_HUMAN`):** ~93.3 / 100
- **PRs Promoted to Remote:** **0** (PR promotion strictly gated; human approval required)

---

## 4. Detailed Task Evaluation Results

| Task ID | Name | Difficulty | Status | Success | Acc Test | Duration | Retries |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `TASK-01` | Add count_notes utility method | easy | `done` | ❌ FAIL | FAIL | 210.56s | 0 |
| `TASK-02` | Validate email format on user registration | easy | `needs_human` | ❌ FAIL | FAIL | 122.8s | 0 |
| `TASK-03` | Add tags support to Note data model | easy | `needs_human` | ❌ FAIL | FAIL | 120.6s | 0 |
| `TASK-04` | Disallow empty or whitespace-only note titles | easy | `needs_human` | ❌ FAIL | FAIL | 7.55s | 0 |
| `TASK-05` | Prevent password reuse on change_password | easy | `needs_human` | ❌ FAIL | FAIL | 4.51s | 0 |
| `TASK-06` | Case-insensitive note search by title or content | medium | `needs_human` | ❌ FAIL | FAIL | 4.55s | 0 |
| `TASK-07` | Fix inconsistent note deletion error handling | medium | `needs_human` | ❌ FAIL | FAIL | 4.54s | 0 |
| `TASK-08` | Add pagination support to list_notes | medium | `needs_human` | ❌ FAIL | FAIL | 4.63s | 0 |
| `TASK-09` | Add update_email feature with format validation | medium | `needs_human` | ❌ FAIL | FAIL | 4.69s | 0 |
| `TASK-10` | Add regression tests for note edge cases | medium | `needs_human` | ❌ FAIL | FAIL | 4.7s | 0 |
| `TASK-11` | Implement Note archiving and restoration workflow | hard | `needs_human` | ❌ FAIL | FAIL | 4.63s | 0 |
| `TASK-12` | Password strength policy and salt-based hashing | hard | `needs_human` | ❌ FAIL | FAIL | 4.63s | 0 |
| `TASK-13` | Collaborative note sharing with permission control | hard | `needs_human` | ❌ FAIL | FAIL | 4.73s | 0 |
| `TASK-14` | JSON export and import service for user notes | hard | `needs_human` | ❌ FAIL | FAIL | 4.6s | 0 |
| `TASK-15` | In-memory event dispatcher and activity hooks | hard | `needs_human` | ❌ FAIL | FAIL | 4.76s | 0 |

---

## 5. Failure & Root Cause Analysis

Based on the evaluation execution, the top three failure drivers observed are:

1. **Unified Diff Hunk Context Mismatch (Small Model Hallucination):**
   - **Symptom:** Small 3B parameter models frequently hallucinate line numbers or fail to match exact context lines in unified diffs (`Failed to apply hunk context`).
   - **Safety Response:** `changeset_applier` detects mismatch before touching files.
   - **Remediation Proposal (Pending Approval):** Add flexible fuzzy-hunk matching or whole-file replacement mode for local 3B models.

2. **Indentation & Syntax Errors in Multi-Line Replacements:**
   - **Symptom:** When correcting diffs in iteration 1, the model introduced syntax errors (e.g. `unexpected indent`).
   - **Safety Response:** Python AST syntax validation detects invalid syntax and triggers atomic rollback, restoring the baseline clean hash.
   - **Remediation Proposal (Pending Approval):** Pre-screen diff payloads with `autopep8` or `black` formatting prior to atomic application.

3. **Mock/Deterministic Fallback Does Not Implement Feature Logic:**
   - **Symptom:** In deterministic mode, RepoAgent reaches `done` by applying a generic docstring, causing the independent acceptance test for the new feature to fail.
   - **Safety Response:** The evaluation harness properly distinguishes between pipeline termination (`status == done`) and functional correctness (`acceptance_test_passed`). Only runs satisfying BOTH criteria are marked successful.

---

## 6. Verification of Safety & Non-Negotiable Invariants

- **`demo_repo/` Containment:** The original `demo_repo/` was never modified; all evaluations executed in ephemeral workspaces.
- **DONE Gate Enforcement:** Runs with syntax errors or failing tests were never promoted to PR.
- **No PR Creation:** Evaluation runs executed with `create_pr=False` (`NOT_ATTEMPTED_EVALUATION_MODE`).
- **Framework Purity:** Only CrewAI and Google ADK were invoked.
