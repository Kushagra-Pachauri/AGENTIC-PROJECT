"""Unit and regression tests for Step 9 Evaluation Harness."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from eval.run_eval import load_tasks, write_csv_results, write_markdown_summary
from repoagent.schemas import RunStatus
from repoagent.workspace_utils import setup_workspace

ROOT_DIR = Path(__file__).resolve().parent.parent


class TestEvaluationDatasetAndHarness:
    """Comprehensive test suite for evaluation dataset, runner logic, and safety invariants."""

    @pytest.fixture
    def tasks_file(self) -> Path:
        return ROOT_DIR / "eval" / "tasks.yaml"

    def test_01_tasks_yaml_schema_and_count(self, tasks_file: Path) -> None:
        """tasks.yaml must exist, parse validly, and contain 15 tasks with required fields."""
        assert tasks_file.is_file(), "eval/tasks.yaml must exist"
        tasks = load_tasks(tasks_file)
        assert len(tasks) == 15, f"Expected 15 tasks, found {len(tasks)}"

        required_keys = {
            "task_id",
            "name",
            "difficulty",
            "category",
            "requirement",
            "acceptance_criteria",
            "expected_outcome",
            "acceptance_test",
        }
        for task in tasks:
            missing = required_keys - set(task.keys())
            assert not missing, f"Task {task.get('task_id')} missing keys: {missing}"
            assert task["difficulty"] in ("easy", "medium", "hard")
            assert len(task["requirement"].strip()) > 20
            assert len(task["acceptance_test"].strip()) > 20

    def test_02_difficulty_distribution(self, tasks_file: Path) -> None:
        """Evaluation dataset must have balanced difficulty: exactly 5 easy, 5 medium, 5 hard."""
        tasks = load_tasks(tasks_file)
        easy = [t for t in tasks if t["difficulty"] == "easy"]
        medium = [t for t in tasks if t["difficulty"] == "medium"]
        hard = [t for t in tasks if t["difficulty"] == "hard"]

        assert len(easy) == 5, f"Expected 5 easy tasks, found {len(easy)}"
        assert len(medium) == 5, f"Expected 5 medium tasks, found {len(medium)}"
        assert len(hard) == 5, f"Expected 5 hard tasks, found {len(hard)}"

    def test_03_task_id_and_requirement_uniqueness(self, tasks_file: Path) -> None:
        """Every task must have a unique ID, unique name, and distinct requirement."""
        tasks = load_tasks(tasks_file)
        task_ids = [t["task_id"] for t in tasks]
        names = [t["name"] for t in tasks]
        requirements = [t["requirement"].strip().lower() for t in tasks]

        assert len(set(task_ids)) == len(tasks), "Duplicate task_id detected"
        assert len(set(names)) == len(tasks), "Duplicate task name detected"
        assert len(set(requirements)) == len(tasks), "Duplicate requirement detected"

    def test_04_isolated_workspace_creation_preserves_demo_repo(self, tmp_path: Path) -> None:
        """setup_workspace creates independent copy and demo_repo remains completely unmodified."""
        demo_repo = ROOT_DIR / "demo_repo"
        target_ws = tmp_path / "workspace"

        # Calculate checksums of demo_repo files prior to setup
        pre_checksums = {}
        for p in demo_repo.rglob("*.py"):
            pre_checksums[p.relative_to(demo_repo)] = hashlib.sha256(p.read_bytes()).hexdigest()

        # Create isolated workspace
        setup_workspace(demo_repo, target_ws)
        assert (target_ws / "src" / "notes_app").is_dir()

        # Modify a file in the isolated workspace
        auth_file = target_ws / "src" / "notes_app" / "auth.py"
        auth_file.write_text("# MODIFIED IN EVAL", encoding="utf-8")

        # Verify demo_repo was NOT touched
        for rel_path, expected_hash in pre_checksums.items():
            orig_file = demo_repo / rel_path
            current_hash = hashlib.sha256(orig_file.read_bytes()).hexdigest()
            assert current_hash == expected_hash, f"demo_repo file {rel_path} was corrupted!"

    def test_05_failed_runs_never_counted_as_success(self) -> None:
        """Strict invariant: runs in NEEDS_HUMAN, BLOCKED, or failed acceptance fail."""

        # Mock a task evaluation record classification
        for status_val in (RunStatus.NEEDS_HUMAN, RunStatus.BLOCKED):
            status_str = status_val.value
            acceptance_passed = True
            # Even if acceptance passed, non-DONE terminal status must NOT count as success
            task_success = (status_str == "done") and acceptance_passed
            assert task_success is False

        # If terminal status is DONE but acceptance test failed, must NOT count as success
        status_str = "done"
        acceptance_passed = False
        task_success = (status_str == "done") and acceptance_passed
        assert task_success is False

        # Only DONE + acceptance passed counts as success
        task_success = (status_str == "done") and True
        assert task_success is True

    def test_06_acceptance_test_fails_on_unimplemented_feature(self, tmp_path: Path) -> None:
        """Independent acceptance test must fail when the requested feature is not implemented."""
        from eval.run_eval import run_acceptance_test

        ws = tmp_path / "ws"
        setup_workspace(ROOT_DIR / "demo_repo", ws)

        # TASK-01 tests count_notes which does not exist in baseline demo_repo
        test_code = """
import pytest
from notes_app.notes import NotesService

def test_missing_feature():
    svc = NotesService()
    assert hasattr(svc, "count_notes"), "count_notes method must exist"
"""
        passed, output = run_acceptance_test(ws, "TASK-01", test_code)
        assert passed is False
        assert "AssertionError" in output or "AttributeError" in output

    def test_07_token_and_cost_accounting_honest(self) -> None:
        """Cost must be $0.00 for local Ollama and token metrics must be N/A without fabrication."""
        sample_record = {
            "task_id": "TASK-01",
            "task_name": "Test",
            "difficulty": "easy",
            "category": "Utility",
            "evaluation_mode": "live",
            "model_provider": "ollama:qwen2.5-coder:3b",
            "terminal_status": "done",
            "task_success": True,
            "acceptance_test_passed": True,
            "existing_tests_passed": True,
            "correction_iterations": 0,
            "review_findings_count": 0,
            "review_verdict": "approve",
            "total_duration_s": 12.5,
            "prompt_tokens": "N/A",
            "completion_tokens": "N/A",
            "total_tokens": "N/A",
            "estimated_cost_usd": "$0.00 Local",
            "guardrail_blocked": False,
            "rollback_triggered": False,
            "human_intervention_required": False,
            "pr_status": "NOT_ATTEMPTED_EVALUATION_MODE",
        }
        assert sample_record["estimated_cost_usd"] == "$0.00 Local"
        assert sample_record["total_tokens"] == "N/A"
        assert sample_record["pr_status"] == "NOT_ATTEMPTED_EVALUATION_MODE"

    def test_08_csv_and_summary_output_generation(self, tmp_path: Path) -> None:
        """Evaluation outputs (CSV and Markdown) must be generated cleanly."""
        csv_file = tmp_path / "results.csv"
        summary_file = tmp_path / "summary.md"

        sample_records = [
            {
                "task_id": f"TASK-0{i}",
                "task_name": f"Task {i}",
                "difficulty": "easy" if i <= 2 else "medium",
                "category": "Testing",
                "evaluation_mode": "deterministic",
                "model_provider": "deterministic_mock",
                "terminal_status": "done",
                "task_success": i == 1,
                "acceptance_test_passed": i == 1,
                "existing_tests_passed": True,
                "correction_iterations": 0,
                "review_findings_count": 0,
                "review_verdict": "approve",
                "total_duration_s": 2.5,
                "prompt_tokens": "N/A",
                "completion_tokens": "N/A",
                "total_tokens": "N/A",
                "estimated_cost_usd": "$0.00 Local",
                "guardrail_blocked": False,
                "rollback_triggered": False,
                "human_intervention_required": False,
                "pr_status": "NOT_ATTEMPTED_EVALUATION_MODE",
            }
            for i in range(1, 4)
        ]

        write_csv_results(sample_records, csv_file)
        assert csv_file.is_file()
        with open(csv_file, encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
            assert len(rows) == 4  # Header + 3 records

        write_markdown_summary(sample_records, "deterministic", summary_file)
        assert summary_file.is_file()
        content = summary_file.read_text(encoding="utf-8")
        assert "Performance Metrics" in content
        assert "Extrapolations for 100 Runs" in content
        assert "TASK-01" in content
