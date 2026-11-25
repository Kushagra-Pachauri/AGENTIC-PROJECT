"""Unit and integration tests for Step 5: Google ADK agents served over A2A."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from repoagent.a2a_client import A2AClient, clean_json_text, extract_text_from_a2a_response
from repoagent.agents.adk.planner import get_planner_agent_card
from repoagent.agents.adk.reviewer import get_reviewer_agent_card
from repoagent.agents.adk.server import create_planner_app, create_reviewer_app
from repoagent.flow import RepoAgentFlow
from repoagent.schemas import (
    ChangeSet,
    Plan,
    PlannedFile,
    PlannerOutput,
    ReviewFinding,
    ReviewReport,
    ReviewVerdict,
    RunState,
    RunStatus,
    TestReport,
)


class TestA2AAgentCards:
    """Verify agent cards are exposed correctly according to A2A specification."""

    def test_planner_agent_card_schema(self) -> None:
        card = get_planner_agent_card("http://localhost:8001/")
        assert card.name == "repo-planner"
        assert "Planner" in card.description
        assert len(card.skills) >= 2
        skill_ids = [s.id for s in card.skills]
        assert "analyze_repo" in skill_ids
        assert "generate_plan" in skill_ids
        assert len(card.supported_interfaces) >= 1
        assert card.supported_interfaces[0].protocol_binding == "JSONRPC"
        assert card.supported_interfaces[0].protocol_version == "1.0"

    def test_reviewer_agent_card_schema(self) -> None:
        card = get_reviewer_agent_card("http://localhost:8002/")
        assert card.name == "code-reviewer"
        assert "Reviewer" in card.description
        assert len(card.skills) >= 1
        assert card.skills[0].id == "review_diff"
        assert len(card.supported_interfaces) >= 1
        assert card.supported_interfaces[0].protocol_binding == "JSONRPC"
        assert card.supported_interfaces[0].protocol_version == "1.0"

    def test_fetch_agent_cards_over_http(self) -> None:
        import concurrent.futures

        def _run() -> None:
            p_app = create_planner_app(port=8001, workspace="demo_repo")
            with TestClient(p_app) as client:
                resp = client.get("/.well-known/agent-card.json")
                assert resp.status_code == 200
                data = resp.json()
                assert data["name"] == "repo-planner"
                assert any(s["id"] == "analyze_repo" for s in data["skills"])

            r_app = create_reviewer_app(port=8002)
            with TestClient(r_app) as client:
                resp = client.get("/.well-known/agent-card.json")
                assert resp.status_code == 200
                data = resp.json()
                assert data["name"] == "code-reviewer"
                assert any(s["id"] == "review_diff" for s in data["skills"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run)
            future.result(timeout=15.0)


class TestA2AClient:
    """Verify A2A client serialization, response extraction, and validation."""

    def test_clean_json_text(self) -> None:
        raw_markdown = '```json\n{"repo_map": "m", "plan": {"summary": "s"}}\n```'
        assert clean_json_text(raw_markdown) == '{"repo_map": "m", "plan": {"summary": "s"}}'
        plain = '{"key": "value"}'
        assert clean_json_text(plain) == plain

    def test_extract_text_from_a2a_response(self) -> None:
        # Artifacts format
        resp_artifacts = {
            "result": {
                "task": {
                    "id": "t1",
                    "artifacts": [{"parts": [{"text": "artifact_content"}]}],
                }
            }
        }
        assert extract_text_from_a2a_response(resp_artifacts) == "artifact_content"

        # History format
        resp_history = {
            "result": {
                "task": {
                    "id": "t2",
                    "history": [
                        {"role": "ROLE_USER", "parts": [{"text": "hello"}]},
                        {"role": "ROLE_AGENT", "parts": [{"text": "agent_reply"}]},
                    ],
                }
            }
        }
        assert extract_text_from_a2a_response(resp_history) == "agent_reply"

        # Direct message format
        resp_msg = {
            "result": {
                "message": {
                    "parts": [{"text": "message_content"}],
                }
            }
        }
        assert extract_text_from_a2a_response(resp_msg) == "message_content"

    def test_extract_text_raises_on_a2a_error(self) -> None:
        err_resp = {"error": {"code": -32602, "message": "Invalid params"}}
        with pytest.raises(RuntimeError, match="Invalid params"):
            extract_text_from_a2a_response(err_resp)

    def test_malformed_response_rejected_by_pydantic_planner(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Return invalid payload missing required fields of PlannerOutput
        mock_resp.json.return_value = {
            "result": {
                "task": {
                    "id": "task-bad",
                    "artifacts": [{"parts": [{"text": '{"wrong_key": 123}'}]}],
                }
            }
        }
        mock_client.post.return_value = mock_resp

        client = A2AClient("http://testserver", http_client=mock_client)
        log_file = tmp_path / "a2a.jsonl"
        with pytest.raises(ValueError, match="Failed to validate PlannerOutput"):
            client.request_plan("Add password reset", workspace="demo_repo", log_file=log_file)

    def test_malformed_response_rejected_by_pydantic_reviewer(self, tmp_path: Path) -> None:
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Return invalid verdict not in enum ("maybe")
        mock_resp.json.return_value = {
            "result": {
                "task": {
                    "id": "task-bad-rev",
                    "artifacts": [
                        {
                            "parts": [
                                {"text": '{"verdict": "maybe", "findings": [], "summary": "hmm"}'}
                            ]
                        }
                    ],
                }
            }
        }
        mock_client.post.return_value = mock_resp

        client = A2AClient("http://testserver", http_client=mock_client)
        plan = Plan(summary="Test plan")
        test_rep = TestReport(command="pytest", passed=True, exit_code=0)

        with pytest.raises(ValueError, match="Failed to validate ReviewReport"):
            client.request_review(plan, "diff", test_rep, log_file=tmp_path / "a2a.jsonl")

    def test_a2a_audit_logging_no_secrets(self, tmp_path: Path) -> None:
        log_file = tmp_path / "runs" / "test_run" / "a2a.jsonl"
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        valid_json = (
            '{"verdict": "approve", "findings": [], '
            '"summary": "All good with token=SECRET_TOKEN_DO_NOT_LOG"}'
        )
        mock_resp.json.return_value = {
            "result": {
                "task": {
                    "id": "task-secret-test",
                    "artifacts": [{"parts": [{"text": valid_json}]}],
                }
            }
        }
        mock_client.post.return_value = mock_resp

        client = A2AClient("http://testserver", http_client=mock_client)
        plan = Plan(summary="Plan with secret context")
        test_rep = TestReport(command="pytest", passed=True, exit_code=0, workspace_hash="hash-123")

        rep = client.request_review(plan, "diff", test_rep, log_file=log_file)
        assert rep.verdict == ReviewVerdict.APPROVE

        assert log_file.is_file()
        lines = log_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["task_id"] == "task-secret-test"
        assert entry["agent_card_url"] == "http://testserver/.well-known/agent-card.json"
        assert "duration_s" in entry
        assert entry["status"] == "success"
        # Verify no auth credentials or secrets logged in standard fields
        assert "authorization" not in entry
        assert "api_key" not in entry


class TestFlowA2AIntegration:
    """Verify Flow orchestration with Planner and Reviewer agents over A2A."""

    def test_flow_planner_called_before_coding(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "dummy.txt").write_text("hello")

        expected_plan = Plan(
            summary="Planner Generated Plan",
            files_to_change=[PlannedFile(path="dummy.txt", reason="Update greeting")],
            steps=["Step 1"],
            risks=["Risk 1"],
            test_strategy="pytest",
        )
        planner_mock = MagicMock(
            return_value=PlannerOutput(repo_map="Map of repo", plan=expected_plan)
        )

        flow = RepoAgentFlow(
            workspace=ws,
            planner_fn=planner_mock,
            coding_fn=lambda s: ChangeSet(summary="Coder change"),
            testing_fn=lambda s: TestReport(
                command="pytest", passed=True, exit_code=0, workspace_hash="h1"
            ),
            review_fn=lambda p, d, t: ReviewReport(
                verdict=ReviewVerdict.APPROVE, summary="Approved"
            ),
        )
        flow.kickoff()

        planner_mock.assert_called_once()
        assert flow.state.repo_map == "Map of repo"
        assert flow.state.plan == expected_plan
        assert flow.state.status == RunStatus.DONE

    def test_flow_review_request_changes_counts_toward_retry_cap(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()

        review_calls = 0

        def custom_review(p: Plan, d: str, t: TestReport) -> ReviewReport:
            nonlocal review_calls
            review_calls += 1
            if review_calls == 1:
                return ReviewReport(
                    verdict=ReviewVerdict.REQUEST_CHANGES,
                    findings=[
                        ReviewFinding(
                            severity="critical",
                            file="auth.py",
                            line=5,
                            message="Static token vulnerability",
                        )
                    ],
                    summary="Security regression detected",
                )
            return ReviewReport(verdict=ReviewVerdict.APPROVE, summary="Fixed")

        coder_iterations = 0

        def custom_coder(s: RunState) -> ChangeSet:
            nonlocal coder_iterations
            coder_iterations += 1
            return ChangeSet(summary=f"Coder attempt {coder_iterations}")

        flow = RepoAgentFlow(
            workspace=ws,
            max_retries=3,
            planner_fn=lambda req, ws: PlannerOutput(
                repo_map="repo", plan=Plan(summary="Test Plan")
            ),
            coding_fn=custom_coder,
            testing_fn=lambda s: TestReport(
                command="pytest", passed=True, exit_code=0, workspace_hash=f"h{coder_iterations}"
            ),
            review_fn=custom_review,
        )
        flow.kickoff()

        # Reviewer rejected once -> retried coder -> reviewer approved -> done
        assert review_calls == 2
        assert coder_iterations == 2
        assert flow.state.retry_count == 1
        assert any("Static token vulnerability" in err for err in flow.state.errors)
        assert flow.state.status == RunStatus.DONE

    def test_flow_review_request_changes_capped_at_max_retries(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()

        # Always reject in review
        def reject_review(p: Plan, d: str, t: TestReport) -> ReviewReport:
            return ReviewReport(
                verdict=ReviewVerdict.REQUEST_CHANGES,
                findings=[
                    ReviewFinding(file="app.py", severity="warning", message="Style violation")
                ],
                summary="Please format",
            )

        flow = RepoAgentFlow(
            workspace=ws,
            initial_state=RunState(max_retries=2),
            planner_fn=lambda req, ws: PlannerOutput(
                repo_map="repo", plan=Plan(summary="Test Plan")
            ),
            coding_fn=lambda s: ChangeSet(summary="Coder change"),
            testing_fn=lambda s: TestReport(
                command="pytest", passed=True, exit_code=0, workspace_hash="h"
            ),
            review_fn=reject_review,
        )
        flow.kickoff()

        # Reached retry cap and terminated with needs_human
        assert flow.state.status == RunStatus.NEEDS_HUMAN
        assert flow.state.retry_count >= 2
        assert any("Execution capped" in err for err in flow.state.errors)

    def test_fixture_not_in_live_path(self) -> None:
        """Verify PASSWORD_RESET_PLAN is not imported or used in flow or scripts."""
        flow_path = Path("src/repoagent/flow.py")
        loop_path = Path("scripts/run_coding_loop.py")

        assert "PASSWORD_RESET_PLAN" not in flow_path.read_text(encoding="utf-8")
        assert "PASSWORD_RESET_PLAN" not in loop_path.read_text(encoding="utf-8")


class TestPlanPathValidation:
    """Verify deterministic plan path validation and retry behaviors."""

    def test_nonexistent_path_rejected(self, tmp_path: Path) -> None:
        from repoagent.workspace_utils import validate_plan_paths

        ws = tmp_path / "ws"
        ws.mkdir()
        src_dir = ws / "src" / "notes_app"
        src_dir.mkdir(parents=True)
        (src_dir / "auth.py").write_text("# auth", encoding="utf-8")

        # Invalid path: src/auth.py does not exist and doesn't match package layout
        invalid_plan = Plan(
            summary="Bad plan",
            files_to_change=[PlannedFile(path="src/auth.py", reason="modify auth")],
        )
        is_valid, errors = validate_plan_paths(invalid_plan, ws)
        assert is_valid is False
        assert len(errors) == 1
        assert "Invalid file path in plan: 'src/auth.py'" in errors[0]

        # Path traversal rejected
        traversal_plan = Plan(
            summary="Traversal plan",
            files_to_change=[PlannedFile(path="../secret.txt", reason="read secret")],
        )
        is_valid_trav, errors_trav = validate_plan_paths(traversal_plan, ws)
        assert is_valid_trav is False
        assert any("Safety violation" in err for err in errors_trav)

    def test_valid_paths_accepted(self, tmp_path: Path) -> None:
        from repoagent.workspace_utils import validate_plan_paths

        ws = tmp_path / "ws"
        ws.mkdir()
        src_dir = ws / "src" / "notes_app"
        src_dir.mkdir(parents=True)
        (src_dir / "auth.py").write_text("# auth", encoding="utf-8")

        valid_plan = Plan(
            summary="Good plan",
            files_to_change=[
                PlannedFile(path="src/notes_app/auth.py", reason="modify existing"),
                PlannedFile(path="tests/test_new_feature.py", reason="add new test file"),
            ],
        )
        is_valid, errors = validate_plan_paths(valid_plan, ws)
        assert is_valid is True
        assert errors == []

    def test_flow_one_retry_then_needs_human(self, tmp_path: Path) -> None:
        """When Planner returns invalid paths twice, flow terminates with needs_human."""
        ws = tmp_path / "ws"
        ws.mkdir()
        src_dir = ws / "src" / "notes_app"
        src_dir.mkdir(parents=True)
        (src_dir / "auth.py").write_text("# auth", encoding="utf-8")

        calls = 0

        def always_bad_planner(req: str, workspace: Path) -> PlannerOutput:
            nonlocal calls
            calls += 1
            return PlannerOutput(
                repo_map="bad repo map",
                plan=Plan(
                    summary="Bad Plan",
                    files_to_change=[PlannedFile(path="src/auth.py", reason="Modify wrong path")],
                ),
            )

        flow = RepoAgentFlow(
            workspace=ws,
            initial_state=RunState(max_retries=3),
            planner_fn=always_bad_planner,
        )
        flow.kickoff()

        # Called once, failed, retried once, failed again -> total 2 attempts
        assert calls == 2
        assert flow.state.status == RunStatus.NEEDS_HUMAN
        assert any("Plan path validation failed on retry" in err for err in flow.state.errors)

    def test_flow_retry_succeeds(self, tmp_path: Path) -> None:
        """When Planner corrects paths on retry, flow continues successfully."""
        ws = tmp_path / "ws"
        ws.mkdir()
        src_dir = ws / "src" / "notes_app"
        src_dir.mkdir(parents=True)
        (src_dir / "auth.py").write_text("# auth", encoding="utf-8")

        calls = 0

        def correcting_planner(req: str, workspace: Path) -> PlannerOutput:
            nonlocal calls
            calls += 1
            if calls == 1:
                return PlannerOutput(
                    repo_map="bad repo map",
                    plan=Plan(
                        summary="Bad Plan",
                        files_to_change=[PlannedFile(path="src/auth.py", reason="Wrong")],
                    ),
                )
            return PlannerOutput(
                repo_map="good repo map",
                plan=Plan(
                    summary="Good Plan",
                    files_to_change=[
                        PlannedFile(path="src/notes_app/auth.py", reason="Correct path")
                    ],
                ),
            )

        flow = RepoAgentFlow(
            workspace=ws,
            initial_state=RunState(max_retries=3),
            planner_fn=correcting_planner,
            coding_fn=lambda s: ChangeSet(summary="Code change"),
            testing_fn=lambda s: TestReport(
                command="pytest", passed=True, exit_code=0, workspace_hash="h"
            ),
            review_fn=lambda p, d, t: ReviewReport(
                verdict=ReviewVerdict.APPROVE,
                findings=[],
                summary="LGTM",
            ),
        )
        flow.kickoff()

        assert calls == 2
        assert flow.state.status == RunStatus.DONE
        assert flow.state.plan.files_to_change[0].path == "src/notes_app/auth.py"
