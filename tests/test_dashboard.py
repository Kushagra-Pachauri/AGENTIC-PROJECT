"""Comprehensive unit and component tests for the RepoAgent execution dashboard.

Verifies:
- Input validation (requirement minimum 5 characters, repo allowlist from env, retries 1-3).
- Default PR authorization is False.
- Terminal & stale run polling termination (no polling re-runs after completion or stale).
- Graceful API connection error handling without unhandled exceptions.
- 100% mocked HTTP clients: zero real run creations, zero subprocesses, zero filesystem mutations.
- Strict 11-stage evidence mapping:
  * Stage 2: Requires actual workspace initialization evidence (not branch alone).
  * Stage 6: Shows UNKNOWN when diff evidence is missing from API summary.
  * Stage 8: Distinguishes retry exhaustion from other NEEDS_HUMAN conditions.
  * Stage 10: Preserves all 7 original DONE Gate criteria + hash parity from done_gate.py.
- Secret sanitization and overview metrics aggregation.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from repoagent.ui.dashboard import (
    PALETTE,
    compute_overview_metrics,
    evaluate_all_stages,
    evaluate_stage_1_requirement,
    evaluate_stage_2_repo_understanding,
    evaluate_stage_6_code_application,
    evaluate_stage_8_retry_loop,
    evaluate_stage_10_done_gate,
    evaluate_stage_11_promotion,
    fetch_health,
    fetch_run_details,
    fetch_run_events,
    fetch_runs,
    format_duration,
    generate_pipeline_html,
    get_allowed_repos,
    get_emerald_theme_css,
    is_terminal_or_stale,
    render_html,
    sanitize_display_text,
    submit_new_run,
    validate_repo_input,
    validate_requirement_input,
    validate_retries_input,
)


class TestDashboardRequirementsAndValidation:
    """Tests for user input validation, allowlists, and PR authorization defaults."""

    def test_01_requirement_validation_min_5_chars(self) -> None:
        """Requirements must be at least 5 non-whitespace characters."""
        assert validate_requirement_input("")[0] is False
        assert validate_requirement_input("   ")[0] is False
        assert validate_requirement_input("fix")[0] is False
        assert validate_requirement_input("1234")[0] is False
        assert validate_requirement_input("  abc ")[0] is False

        # 5 characters or more
        valid, msg = validate_requirement_input("12345")
        assert valid is True
        assert msg == ""

        valid, msg = validate_requirement_input("Add user notes search")
        assert valid is True
        assert msg == ""

    def test_02_repo_allowlist_env_configuration(self) -> None:
        """Repo allowlist reads from REPOAGENT_UI_REPOS with demo_repo as default."""
        with patch.dict(os.environ, {}, clear=True):
            allowed = get_allowed_repos()
            assert allowed == ["demo_repo"]
            assert validate_repo_input("demo_repo")[0] is True
            assert validate_repo_input("other_repo")[0] is False

        with patch.dict(os.environ, {"REPOAGENT_UI_REPOS": "repo_a, repo_b, repo_c"}):
            allowed_custom = get_allowed_repos()
            assert allowed_custom == ["repo_a", "repo_b", "repo_c"]
            assert validate_repo_input("repo_b")[0] is True
            assert validate_repo_input("demo_repo")[0] is False

        # Path traversal and absolute paths strictly rejected
        assert validate_repo_input("../demo_repo")[0] is False
        assert validate_repo_input("..\\demo_repo")[0] is False
        assert validate_repo_input("/etc/passwd")[0] is False
        assert validate_repo_input("C:\\Windows")[0] is False

    def test_03_max_retries_restricted_to_1_to_3(self) -> None:
        """Max correction retries must be bounded strictly in [1, 3]."""
        assert validate_retries_input(1)[0] is True
        assert validate_retries_input(2)[0] is True
        assert validate_retries_input(3)[0] is True

        assert validate_retries_input(0)[0] is False
        assert validate_retries_input(4)[0] is False
        assert validate_retries_input(-1)[0] is False

    def test_04_pr_authorization_defaults_to_false(self) -> None:
        """New run payload defaults create_pr to False unless explicitly authorized."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"run_id": "run_test_01", "status": "running"}

        with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
            success, res = submit_new_run(
                repo_url="demo_repo",
                requirement="Valid requirement text",
                max_retries=3,
                create_pr=False,
            )
            assert success is True
            mock_post.assert_called_once()
            payload = mock_post.call_args[1]["json"]
            assert payload["create_pr"] is False
            assert payload["max_retries"] == 3
            assert payload["repo_url"] == "demo_repo"

        # Explicitly authorized
        with patch("httpx.Client.post", return_value=mock_resp) as mock_post_auth:
            success, res = submit_new_run(
                repo_url="demo_repo",
                requirement="Valid requirement text",
                max_retries=2,
                create_pr=True,
            )
            assert success is True
            payload_auth = mock_post_auth.call_args[1]["json"]
            assert payload_auth["create_pr"] is True


class TestPollingLifecycleAndNetworkResilience:
    """Tests verifying polling stoppage on terminal runs and network error handling."""

    def test_05_no_refresh_after_terminal_or_stale_status(self) -> None:
        """Polling stops completely for terminal runs or stale runs."""
        # Terminal statuses must evaluate to terminal (no polling)
        assert is_terminal_or_stale({"status": "done", "is_stale": False}) is True
        assert is_terminal_or_stale({"status": "needs_human", "is_stale": False}) is True
        assert is_terminal_or_stale({"status": "blocked", "is_stale": False}) is True
        assert is_terminal_or_stale({"status": "failed", "is_stale": False}) is True

        # Stale run must evaluate to terminal (no polling)
        assert is_terminal_or_stale({"status": "running", "is_stale": True}) is True

        # Non-stale running is active
        assert is_terminal_or_stale({"status": "running", "is_stale": False}) is False

    def test_06_api_connection_refused_handling(self) -> None:
        """fetch_health and fetch_runs handle connection errors gracefully."""
        with patch("httpx.Client.get", side_effect=Exception("Connection Refused")):
            assert fetch_health("http://localhost:8000") is None
            assert fetch_runs("http://localhost:8000") == []
            assert fetch_run_details("run_123", "http://localhost:8000") is None
            assert fetch_run_events("run_123", api_url="http://localhost:8000") == []

        # submit_new_run handles connection error cleanly
        with patch("httpx.Client.post", side_effect=Exception("Connection Refused")):
            success, msg = submit_new_run("demo_repo", "Valid requirement", max_retries=3)
            assert success is False
            assert "Failed to connect to backend" in str(msg)


class TestStageEvidenceMapping:
    """Tests verifying exact mapping against real backend fields and events."""

    def test_07_stage_2_requires_actual_workspace_initialization_evidence(self) -> None:
        """A branch name alone must NOT mark repository understanding as COMPLETED."""
        # Only branch exists, but no workspace initialization event
        run_data_branch_only = {"branch": "repoagent/run_123", "repo_url": "demo_repo"}
        events_empty: list[dict] = []
        status, details = evaluate_stage_2_repo_understanding(run_data_branch_only, events_empty)
        assert status == "NOT AVAILABLE"
        assert "not confirmed" in details

        # Completed analyze task event present
        events_with_task = [{"event_type": "TASK_COMPLETED", "stage": "analyze"}]
        status, details = evaluate_stage_2_repo_understanding(
            run_data_branch_only, events_with_task
        )
        assert status == "COMPLETED"

    def test_08_stage_6_requires_explicit_application_evidence(self) -> None:
        """Absence of error is insufficient; requires explicit diff/application evidence."""
        # Test reports exist, but no diff in events or API summary
        run_data = {
            "test_reports": [{"passed": True, "exit_code": 0}],
            "errors": [],
            "rejected_changes_count": 0,
        }
        events_without_diff = [
            {"event_type": "TASK_COMPLETED", "stage": "code", "output_summary": {}}
        ]
        status, details = evaluate_stage_6_code_application(run_data, events_without_diff)
        assert status == "UNKNOWN"
        assert "not available in API summary" in details

        # Explicit diff verified in output_summary
        events_with_diff = [
            {
                "event_type": "TASK_COMPLETED",
                "stage": "code",
                "output_summary": {"diff": "@@ -1 +1 @@\n+change"},
            }
        ]
        status_ok, details_ok = evaluate_stage_6_code_application(run_data, events_with_diff)
        assert status_ok == "COMPLETED"

    def test_09_stage_8_distinguishes_retry_exhaustion_from_other_conditions(self) -> None:
        """Stage 8 marks NEEDS_HUMAN only when retries are actually exhausted."""
        # 1. First-pass success (0 retries)
        run_data_clean = {
            "retry_count": 0,
            "max_retries": 3,
            "test_reports": [{"passed": True}],
            "errors": [],
        }
        status, details = evaluate_stage_8_retry_loop(run_data_clean, [])
        assert status == "SKIPPED"

        # 2. NEEDS_HUMAN caused by guardrail flag (not retry exhaustion)
        run_data_flagged = {
            "status": "needs_human",
            "retry_count": 0,
            "max_retries": 3,
            "test_reports": [],
            "errors": ["User requirement flagged by input guardrail for human review"],
        }
        status_flagged, _ = evaluate_stage_8_retry_loop(run_data_flagged, [])
        assert status_flagged == "NOT APPLICABLE"

        # 3. True retry exhaustion
        run_data_exhausted = {
            "status": "needs_human",
            "retry_count": 3,
            "max_retries": 3,
            "test_reports": [{"passed": False, "exit_code": 1}],
            "errors": ["Execution capped: maximum retries reached after test failure."],
        }
        status_ex, details_ex = evaluate_stage_8_retry_loop(run_data_exhausted, [])
        assert status_ex == "NEEDS_HUMAN"
        assert "Correction budget exhausted" in details_ex

    def test_10_stage_10_preserves_all_7_original_criteria(self) -> None:
        """Authoritative DONE Gate checklist accurately displays all 7 criteria + hash parity."""
        run_data_done = {
            "status": "done",
            "repo_url": "runs/run_abc/workspace",
            "test_reports": [
                {
                    "passed": True,
                    "exit_code": 0,
                    "failed_tests": [],
                    "workspace_hash": "a1b2c3d4e5f6",
                }
            ],
            "review": {"verdict": "approve"},
        }
        status, checklist, summary = evaluate_stage_10_done_gate(run_data_done)
        assert status == "COMPLETED"
        assert "Authoritative Verdict: PASS" in summary

        # Verify all 7 conditions + hash parity are in the checklist
        assert "1. Status Eligibility (Not NEEDS_HUMAN/BLOCKED)" in checklist
        assert checklist["1. Status Eligibility (Not NEEDS_HUMAN/BLOCKED)"] == "PASS"

        assert "2. Deterministic Test Pass (exit_code == 0)" in checklist
        assert checklist["2. Deterministic Test Pass (exit_code == 0)"] == "PASS"

        assert "3. Valid Non-Empty Workspace Hash" in checklist
        assert checklist["3. Valid Non-Empty Workspace Hash"] == "PASS"

        assert "4. Zero Failed Tests in Latest Report" in checklist
        assert checklist["4. Zero Failed Tests in Latest Report"] == "PASS"

        assert "5. Uncommitted Workspace Modifications" in checklist
        assert "NOT AVAILABLE IN API" in checklist["5. Uncommitted Workspace Modifications"]

        assert "6. Workspace Isolation (Not Pristine demo_repo)" in checklist
        assert (
            "NOT AVAILABLE IN API" in checklist["6. Workspace Isolation (Not Pristine demo_repo)"]
        )

        # When actual workspace isolation evidence IS provided, it evaluates to PASS
        run_data_with_ws = dict(run_data_done, workspace_isolated=True)
        _, checklist_ws, _ = evaluate_stage_10_done_gate(run_data_with_ws)
        assert checklist_ws["6. Workspace Isolation (Not Pristine demo_repo)"] == "PASS"

        assert "7. Reviewer Explicit Approval (verdict == APPROVE)" in checklist
        assert checklist["7. Reviewer Explicit Approval (verdict == APPROVE)"] == "PASS"

        assert "8. Live Disk Hash Parity" in checklist
        assert "NOT AVAILABLE IN API" in checklist["8. Live Disk Hash Parity"]

    def test_11_stage_1_and_all_11_stages_synthesis(self) -> None:
        """evaluate_all_stages produces exact 11 structured descriptors."""
        run_data = {
            "run_id": "run_full_test",
            "status": "done",
            "requirement": "Add note search feature",
            "repo_url": "runs/run_full_test/workspace",
            "branch": "repoagent/run_full_test",
            "test_reports": [
                {
                    "passed": True,
                    "exit_code": 0,
                    "failed_tests": [],
                    "workspace_hash": "hash123",
                    "duration_s": 0.5,
                }
            ],
            "review": {"verdict": "approve", "findings": []},
            "pr": {
                "url": "https://github.com/test/repo/pull/1",
                "branch": "repoagent/run_full_test",
            },
            "retry_count": 0,
            "max_retries": 3,
            "rejected_changes_count": 0,
        }
        events = [
            {"event_type": "INPUT_ALLOWED"},
            {"event_type": "TASK_COMPLETED", "stage": "analyze"},
            {"event_type": "AGENT_COMPLETED", "agent_name": "planner", "stage": "plan"},
            {
                "event_type": "TASK_COMPLETED",
                "stage": "code",
                "output_summary": {"diff": "+diff"},
            },
            {"event_type": "CHANGESET_VALIDATION_PASSED"},
        ]

        stages = evaluate_all_stages(run_data, events)
        assert len(stages) == 11
        stage_names = [s["name"] for s in stages]
        assert "Requirement Analysis" in stage_names
        assert "Repository Understanding" in stage_names
        assert "Planning Agent (ADK A2A)" in stage_names
        assert "Coding Agent (CrewAI)" in stage_names
        assert "ChangeSet Guardrails" in stage_names
        assert "Code Application" in stage_names
        assert "Docker Sandbox Testing" in stage_names
        assert "Correction & Retry Loop" in stage_names
        assert "Independent Code Review" in stage_names
        assert "Authoritative DONE Gate" in stage_names
        assert "GitHub Promotion" in stage_names

        # Verification: Stage 1 status is COMPLETED
        s1_st, _ = evaluate_stage_1_requirement(run_data, events)
        assert s1_st == "COMPLETED"

    def test_14_stage_11_pr_promotion_evidence_distinction(self) -> None:
        """Stage 11 distinguishes all 5 promotion evidence states accurately."""
        # 1. PR URL successfully recorded -> COMPLETED
        run_with_pr = {
            "status": "done",
            "pr": {"url": "https://github.com/org/repo/pull/42", "branch": "repoagent/feat"},
        }
        st1, det1 = evaluate_stage_11_promotion(run_with_pr)
        assert st1 == "COMPLETED"
        assert "https://github.com/org/repo/pull/42" in det1

        # 2. PR creation error in run errors -> FAILED
        run_pr_err = {"status": "needs_human", "errors": ["Git push failed: PR creation rejected."]}
        st2, det2 = evaluate_stage_11_promotion(run_pr_err)
        assert st2 == "FAILED"
        assert "Pull Request promotion failed" in det2

        # 3. Explicit authorization evidence: create_pr is False -> SKIPPED
        run_explicit_no_pr = {"status": "done", "create_pr": False}
        st3, det3 = evaluate_stage_11_promotion(run_explicit_no_pr)
        assert st3 == "SKIPPED"
        assert "PR creation was not authorized" in det3

        # 4. Status == done but missing explicit authorization evidence -> UNKNOWN
        run_done_no_evidence = {"status": "done"}
        st4, det4 = evaluate_stage_11_promotion(run_done_no_evidence)
        assert st4 == "UNKNOWN"
        assert det4 == "PR status unavailable."

        # 5. Pending gate / in progress -> PENDING
        run_running = {"status": "running"}
        st5, det5 = evaluate_stage_11_promotion(run_running)
        assert st5 == "PENDING"
        assert "Promotion waiting" in det5


class TestFormattingAndSanitization:
    """Tests verifying secret redaction, metrics aggregation, and duration display."""

    def test_12_credential_sanitization(self) -> None:
        """Secrets are safely redacted from logs and texts."""
        raw_token = "ghp_abcdefghijklmnopqrstuvwxyz012345"
        raw_sk = "sk-1234567890abcdefghijklmnopqrstuvwxyz"
        sensitive = f"Failed with {raw_token} and {raw_sk}"

        clean = sanitize_display_text(sensitive)
        assert raw_token not in clean
        assert raw_sk not in clean
        assert "[REDACTED" in clean

    def test_13_duration_and_metrics_helpers(self) -> None:
        """format_duration and compute_overview_metrics work as expected."""
        assert format_duration(None) == "N/A"
        assert "15.00 s" in format_duration(15.0)
        assert format_duration(125.0) == "2m 5s"
        assert "1.5 hours" in format_duration(5400.0)

        runs = [
            {"status": "done"},
            {"status": "needs_human"},
            {"status": "blocked"},
            {"status": "running"},
            {"status": "failed"},
        ]
        metrics = compute_overview_metrics(runs)
        assert metrics["total"] == 5
        assert metrics["completed"] == 1
        assert metrics["needs_human"] == 1
        assert metrics["blocked"] == 1
        assert metrics["running"] == 1
        assert metrics["failed"] == 1


class TestEmeraldDarkTheme:
    """Tests verifying Emerald Dark Design System tokens and connected pipeline HTML generation."""

    def test_14_palette_design_tokens(self) -> None:
        """Palette contains reference-inspired emerald dark tokens."""
        assert PALETTE["bg_main"] == "#05090B"
        assert PALETTE["surface_primary"] == "#0D1115"
        assert PALETTE["surface_card"] == "#101419"
        assert PALETTE["border_emerald"] == "#176B42"
        assert PALETTE["emerald_primary"] == "#16A34A"
        assert PALETTE["neon_accent"] == "#35D878"

    def test_15_css_stylesheet_content(self) -> None:
        """CSS contains grid pattern, glow shadows, and font overrides."""
        css = get_emerald_theme_css()
        assert "#05090B" in css
        assert "radial-gradient" in css
        assert "linear-gradient" in css
        assert "workflow-canvas" in css
        assert "pipeline-node-card" in css
        assert "node-running" in css
        assert "pulse-glow" in css

    def test_16_generate_pipeline_html_structure(self) -> None:
        """Connected horizontal pipeline HTML renders nodes, connectors, and active states."""
        stages = [
            {"id": 1, "name": "Requirement Analysis", "status": "COMPLETED"},
            {"id": 2, "name": "Repository Understanding", "status": "RUNNING"},
            {"id": 3, "name": "Planning Agent", "status": "PENDING"},
        ]
        run_data = {
            "run_id": "run_test_emerald_01",
            "status": "running",
            "branch": "main",
            "duration_s": 42.5,
            "pr": None,
        }

        html_out = generate_pipeline_html(stages, run_data)
        assert "workflow-canvas" in html_out
        assert "run_test_emerald_01"[-7:] in html_out
        assert "In progress" in html_out
        assert "42.50 s" in html_out
        assert "node-completed" in html_out
        assert "node-running" in html_out
        assert "node-pending" in html_out
        assert "node-connector-line" in html_out
        assert "connector-active" in html_out
        assert html_out.startswith('<div class="workflow-canvas">')

    def test_17_render_html_helper(self) -> None:
        """render_html helper strips indentation and calls st.html or st.markdown."""
        sample_raw = """
            <div class="test">
                <span>Value</span>
            </div>
        """
        with patch("streamlit.html", create=True) as mock_st_html:
            render_html(sample_raw)
            mock_st_html.assert_called_once()
            called_arg = mock_st_html.call_args[0][0]
            assert called_arg.startswith('<div class="test">')
            assert not called_arg.startswith(" ")
