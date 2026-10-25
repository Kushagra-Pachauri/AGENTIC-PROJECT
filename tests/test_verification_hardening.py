"""Tests for deterministic testing, duplicate test rejection, and workspace hash binding."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from repoagent.changeset_applier import (
    apply_changeset_to_workspace,
    workspace_has_modifications,
)
from repoagent.flow import RepoAgentFlow
from repoagent.schemas import (
    ChangeSet,
    FileChange,
    Plan,
    PlannedFile,
    ReviewReport,
    ReviewVerdict,
    RunState,
    RunStatus,
    TestReport,
)
from repoagent.workspace_utils import (
    compute_workspace_hash,
    find_duplicate_test_functions,
    setup_workspace,
)


def test_workspace_hash_changes_when_file_changes(tmp_path: Path) -> None:
    """Workspace hash must uniquely bind to file contents and ignore byte-code caches."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    file_a = src_dir / "a.py"
    file_b = src_dir / "b.py"

    file_a.write_text("def a(): return 1\n", encoding="utf-8")
    file_b.write_text("def b(): return 2\n", encoding="utf-8")

    h1 = compute_workspace_hash(tmp_path)
    assert h1
    assert len(h1) == 64

    # Modifying a file must change the hash
    file_a.write_text("def a(): return 999\n", encoding="utf-8")
    h2 = compute_workspace_hash(tmp_path)
    assert h1 != h2

    # Reverting must restore the hash
    file_a.write_text("def a(): return 1\n", encoding="utf-8")
    assert compute_workspace_hash(tmp_path) == h1

    # Adding an untracked file must change the hash
    file_c = src_dir / "c.py"
    file_c.write_text("def c(): return 3\n", encoding="utf-8")
    h3 = compute_workspace_hash(tmp_path)
    assert h3 != h1

    # Adding byte-code / cache files must NOT change the hash
    cache_dir = src_dir / "__pycache__"
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / "a.cpython-311.pyc").write_bytes(b"compiled-bytecode-data")
    assert compute_workspace_hash(tmp_path) == h3


def test_duplicate_test_function_name_rejected(tmp_path: Path) -> None:
    """Duplicate test names in a test file must be atomically rejected."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    test_file = tests_dir / "test_example.py"
    initial_code = "class TestSample:\n    def test_alpha(self) -> None:\n        assert True\n"
    test_file.write_text(initial_code, encoding="utf-8")

    # ChangeSet attempting to define test_alpha a second time in TestSample
    duplicate_diff = (
        "@@ -1,3 +1,6 @@\n"
        " class TestSample:\n"
        "     def test_alpha(self) -> None:\n"
        "         assert True\n"
        "+    def test_alpha(self) -> None:\n"
        "+        assert 1 == 1\n"
    )
    changeset = ChangeSet(
        files=[
            FileChange(
                path="tests/test_example.py",
                action="modify",
                diff=duplicate_diff,
            )
        ],
        summary="Introduce duplicate test_alpha",
    )

    with pytest.raises(ValueError, match="Duplicate test function detected"):
        apply_changeset_to_workspace(changeset, tmp_path)

    # Verify atomic rollback left file in pristine initial state
    assert test_file.read_text(encoding="utf-8") == initial_code
    assert find_duplicate_test_functions(tmp_path) == []


def test_fake_testing_agent_cannot_falsify_failed_tool_results(tmp_path: Path) -> None:
    """Flow test step must derive TestReport strictly from the tool and reject fake LLM claims."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "auth.py").write_text("def login(): pass\n", encoding="utf-8")

    real_failing_report = TestReport(
        command="pytest",
        passed=False,
        exit_code=1,
        failed_tests=["tests/test_auth.py::test_fail"],
        log_tail="FAILED tests/test_auth.py::test_fail - AssertionError",
        duration_s=1.2,
        raw_output="FAILED tests/test_auth.py::test_fail - AssertionError\n1 failed in 1.2s",
    )

    state = RunState(
        run_id="verif123",
        requirement="Verify test isolation",
        max_retries=1,
    )

    flow = RepoAgentFlow(
        initial_state=state,
        workspace=tmp_path,
        use_live_agents=True,
    )

    # Patch test_runner MCP to return the real failing report
    patch_runner = patch(
        "repoagent.mcp_servers.test_runner.run_tests",
        return_value=real_failing_report,
    )
    with (
        patch_runner as mock_run,
        patch("repoagent.flow.workspace_has_modifications", return_value=True),
    ):
        result = flow.test()

    mock_run.assert_called_once_with("pytest")
    assert result == "test_completed"
    assert len(flow.state.test_reports) == 1

    final_report = flow.state.test_reports[0]
    assert final_report.passed is False
    assert final_report.exit_code == 1
    assert "tests/test_auth.py::test_fail" in final_report.failed_tests
    assert final_report.workspace_hash  # bound to workspace hash

    # Routing must route to retry/needs_human, NEVER pass
    next_step = flow.route_after_test()
    assert next_step != "pass"
    assert flow.state.status != RunStatus.DONE


class TestCorrectnessGatingDefects:
    """Regression tests for RepoAgent correctness-gating defects from run 97e2a052c992."""

    def test_bypass_prevention_isolates_workspace(self, tmp_path: Path) -> None:
        """RepoAgentFlow must never execute against the pristine demo_repo directly."""
        import os
        import stat
        import uuid

        from repoagent.workspace_utils import get_runs_dir

        run_id = f"test_iso_{uuid.uuid4().hex[:10]}"
        run_dir = get_runs_dir() / run_id
        try:
            state = RunState(
                run_id=run_id,
                requirement="Add password reset",
                repo_url="demo_repo",
            )
            flow = RepoAgentFlow(
                initial_state=state,
                workspace="demo_repo",
                use_live_agents=False,
            )
            resolved_ws = flow._get_workspace_path()

            # Must NOT be demo_repo
            assert resolved_ws != Path("demo_repo").resolve()
            # Must be an isolated run directory workspace
            assert Path(resolved_ws).resolve() == (run_dir / "workspace").resolve()
            assert resolved_ws.is_dir()
            assert (resolved_ws / ".git").is_dir()
            assert (resolved_ws / "src" / "notes_app" / "auth.py").is_file()
        finally:
            if run_dir.exists():

                def _rm_readonly(func, p, _):
                    os.chmod(p, stat.S_IWRITE)
                    func(p)

                shutil.rmtree(run_dir, onerror=_rm_readonly)

    def test_malformed_diff_rejected(self, tmp_path: Path) -> None:
        """Malformed diff or zero-modification diff must be rejected with atomic rollback."""
        src_dir = tmp_path / "src" / "notes_app"
        src_dir.mkdir(parents=True)
        auth_file = src_dir / "auth.py"
        orig_content = "class Authenticator:\n    def login(self): pass\n"
        auth_file.write_text(orig_content, encoding="utf-8")

        # Case 1: Diff where modify produces zero modifications
        no_op_diff = "@@ -1,2 +1,2 @@\n class Authenticator:\n     def login(self): pass\n"
        cs_noop = ChangeSet(
            files=[FileChange(path="src/notes_app/auth.py", action="modify", diff=no_op_diff)],
            summary="Zero-modification diff",
        )
        with pytest.raises(ValueError, match="produced no modifications"):
            apply_changeset_to_workspace(cs_noop, tmp_path)
        assert auth_file.read_text(encoding="utf-8") == orig_content

        # Case 2: Truncated diff with syntax error (unclosed string literal from 97e2a052c992)
        broken_diff = (
            "@@ -1,2 +1,5 @@\n"
            " class Authenticator:\n"
            "+    def reset_password(self, token, new_password):\n"
            '+        """Unclosed string literal\n'
            "     def login(self): pass\n"
        )
        cs_broken = ChangeSet(
            files=[FileChange(path="src/notes_app/auth.py", action="modify", diff=broken_diff)],
            summary="Truncated diff with unclosed docstring",
        )
        with pytest.raises(ValueError, match=r"(?i)syntax"):
            apply_changeset_to_workspace(cs_broken, tmp_path)
        assert auth_file.read_text(encoding="utf-8") == orig_content

    def test_syntax_error_in_injected_method_rejected(self, tmp_path: Path) -> None:
        """Syntax error in injected class methods must fail validation without touching disk."""
        src_dir = tmp_path / "src" / "notes_app"
        src_dir.mkdir(parents=True)
        app_file = src_dir / "app.py"
        orig_content = "class NotesApp:\n    def run(self): pass\n"
        app_file.write_text(orig_content, encoding="utf-8")

        syntax_error_diff = (
            "@@ -1,2 +1,5 @@\n"
            " class NotesApp:\n"
            "+    def broken_syntax(self\n"
            "+        invalid syntax\n"
            "     def run(self): pass\n"
        )
        cs = ChangeSet(
            files=[
                FileChange(
                    path="src/notes_app/app.py",
                    action="modify",
                    diff=syntax_error_diff,
                )
            ],
            summary="Invalid python syntax",
        )
        with pytest.raises(ValueError, match=r"(?i)syntax"):
            apply_changeset_to_workspace(cs, tmp_path)
        assert app_file.read_text(encoding="utf-8") == orig_content

    def test_unchanged_workspace_fails_test_verification(self, tmp_path: Path) -> None:
        """Unchanged workspace must fail verification:no_changes_applied."""
        # Initialize an isolated git workspace with pristine demo_repo
        ws = setup_workspace("demo_repo", tmp_path / "workspace")
        assert not workspace_has_modifications(ws)

        state = RunState(
            run_id="test_unchanged",
            requirement="Add password reset",
            max_retries=1,
        )
        flow = RepoAgentFlow(
            initial_state=state,
            workspace=ws,
            use_live_agents=False,
        )

        result = flow.test()
        assert result == "test_completed"
        assert len(flow.state.test_reports) == 1
        last_report = flow.state.test_reports[-1]
        assert last_report.passed is False
        assert "verification:no_changes_applied" in last_report.failed_tests

    def test_missing_feature_tests_fails_test_verification(self, tmp_path: Path) -> None:
        """When plan specifies test changes but none modified, verification fails."""
        ws = setup_workspace("demo_repo", tmp_path / "workspace")

        # Modify only an implementation file, NOT a test file
        auth_file = ws / "src" / "notes_app" / "auth.py"
        auth_file.write_text(
            auth_file.read_text(encoding="utf-8") + "\n# Modified implementation\n",
            encoding="utf-8",
        )
        assert workspace_has_modifications(ws)

        state = RunState(
            run_id="test_missing_tests",
            requirement="Add password reset",
            plan=Plan(
                summary="Plan with tests",
                files_to_change=[
                    PlannedFile(path="src/notes_app/auth.py", reason="Add logic"),
                    PlannedFile(path="tests/test_auth.py", reason="Add tests"),
                ],
            ),
        )
        flow = RepoAgentFlow(
            initial_state=state,
            workspace=ws,
            use_live_agents=False,
        )

        mock_passing = TestReport(
            command="pytest tests/ -v",
            passed=True,
            exit_code=0,
            duration_s=0.5,
            raw_output="12 passed in 0.5s",
        )
        with patch("repoagent.mcp_servers.test_runner.run_tests", return_value=mock_passing):
            result = flow.test()

        assert result == "test_completed"
        last_report = flow.state.test_reports[-1]
        assert last_report.passed is False
        assert "verification:missing_feature_tests" in last_report.failed_tests
        assert any("no test files were added or modified" in err for err in flow.state.errors)

    def test_false_done_status_blocked(self, tmp_path: Path) -> None:
        """open_pr must block status=DONE if any gating invariants are violated."""
        ws = setup_workspace("demo_repo", tmp_path / "workspace")

        passing_report = TestReport(
            command="pytest",
            passed=True,
            exit_code=0,
            duration_s=1.0,
            workspace_hash="testhash123456",
        )
        approved_review = ReviewReport(
            verdict=ReviewVerdict.APPROVE,
            findings=[],
            summary="Approved",
        )

        # Invariant 1: Unchanged workspace must be blocked
        state1 = RunState(
            run_id="run_gating_1",
            test_reports=[passing_report],
            review=approved_review,
        )
        flow1 = RepoAgentFlow(initial_state=state1, workspace=ws, use_live_agents=False)
        flow1.open_pr()
        assert flow1.state.status == RunStatus.NEEDS_HUMAN

        # Modify a file on disk
        (ws / "src" / "notes_app" / "auth.py").write_text("# mod\n", encoding="utf-8")
        assert workspace_has_modifications(ws)

        # Invariant 2: Workspace is baseline demo_repo must be blocked
        state2 = RunState(
            run_id="run_gating_2",
            test_reports=[passing_report],
            review=approved_review,
        )
        flow2 = RepoAgentFlow(
            initial_state=state2,
            workspace=Path("demo_repo").resolve(),
            use_live_agents=False,
        )
        flow2.open_pr()
        assert flow2.state.status == RunStatus.NEEDS_HUMAN

        # Invariant 3: Test report missing workspace_hash must be blocked
        no_hash_report = TestReport(
            command="pytest",
            passed=True,
            exit_code=0,
            duration_s=1.0,
            workspace_hash="",
        )
        state3 = RunState(
            run_id="run_gating_3",
            test_reports=[no_hash_report],
            review=approved_review,
        )
        flow3 = RepoAgentFlow(initial_state=state3, workspace=ws, use_live_agents=False)
        flow3.open_pr()
        assert flow3.state.status == RunStatus.NEEDS_HUMAN

        # Invariant 4: Review requested changes must be blocked
        state4 = RunState(
            run_id="run_gating_4",
            test_reports=[passing_report],
            review=ReviewReport(
                verdict=ReviewVerdict.REQUEST_CHANGES,
                findings=[],
                summary="Changes requested",
            ),
        )
        flow4 = RepoAgentFlow(initial_state=state4, workspace=ws, use_live_agents=False)
        flow4.open_pr()
        assert flow4.state.status == RunStatus.NEEDS_HUMAN

        # Valid Case: Passing tests, modified isolated workspace, approved review, no errors
        state5 = RunState(
            run_id="run_gating_5",
            test_reports=[passing_report],
            review=approved_review,
        )
        flow5 = RepoAgentFlow(initial_state=state5, workspace=ws, use_live_agents=False)
        flow5.open_pr()
        assert flow5.state.status == RunStatus.DONE

    def test_repoagent_runs_dir_isolation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify that setting REPOAGENT_RUNS_DIR isolates run folders from ./runs/."""
        custom_runs = tmp_path / "custom_runs"
        monkeypatch.setenv("REPOAGENT_RUNS_DIR", str(custom_runs))

        # 1. Flow fallback workspace creation uses custom_runs
        initial_state = RunState(requirement="Isolation check")
        flow = RepoAgentFlow(initial_state=initial_state)
        ws = flow._get_workspace_path()
        assert str(custom_runs) in str(ws)

        # 2. Checkpoint saves to custom_runs, not ./runs
        flow._save_checkpoint()
        assert (custom_runs / initial_state.run_id / "run_state.json").is_file()

    def test_cli_cannot_inject_mock_test_shortcut(self) -> None:
        """Verify CLI cannot activate is_mock_test and unchanged workspace cannot reach DONE."""
        from repoagent.run import build_parser

        parser = build_parser()
        # Verify parser has no options for mock injection
        dest_names = [action.dest for action in parser._actions]
        assert "coding_fn" not in dest_names
        assert "testing_fn" not in dest_names
        assert "mock" not in dest_names

        # Verify default Flow instantiated without mock functions has is_mock_test = False
        state = RunState(requirement="Verify mock guard")
        flow = RepoAgentFlow(initial_state=state)
        assert flow.coding_fn is None
        assert flow.testing_fn is None
        is_mock_test = flow.coding_fn is not None and flow.testing_fn is not None
        assert is_mock_test is False
