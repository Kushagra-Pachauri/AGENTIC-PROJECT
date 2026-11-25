"""Comprehensive verification suite for RepoAgent standalone showcase dashboard and historical runs.

Tests:
1. All curated runs load correctly.
2. Status filters work.
3. Search works.
4. Detailed run inspection works across all curated runs.
5. Missing or incomplete artifacts fail gracefully.
6. Secret sanitization tests pass.
7. Existing successful run 0f04e14907d7 remains correctly represented.
8. PR #7 link resolves correctly.
9. Public Showcase Mode continues working without Docker, Ollama or FastAPI.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Force demo/showcase mode with offline API
os.environ["REPOAGENT_DEMO_MODE"] = "true"
os.environ["REPOAGENT_API_URL"] = "http://offline-backend:9999"

from repoagent.ui.dashboard import (
    evaluate_all_stages,
    fetch_health,
    fetch_run_details,
    fetch_run_events,
    fetch_runs,
    generate_pipeline_html,
)


def test_1_curated_runs_loaded():
    """1. All curated runs load correctly from showcase_data."""
    runs = fetch_runs(api_url="http://offline-backend:9999")
    assert len(runs) >= 6, f"Expected at least 6 curated runs, found {len(runs)}"
    run_ids = [r["run_id"] for r in runs]

    # Required priority runs
    assert "0f04e14907d7" in run_ids, "0f04e14907d7 (Live successful) must be present"
    assert "0f198f159c51" in run_ids, "0f198f159c51 (Live quota/timeout failure) must be present"
    assert "90c69b2170f4" in run_ids, "90c69b2170f4 (Live test failure/needs_human) must be present"
    assert "run_b82681b26b95" in run_ids, "run_b82681b26b95 (Deterministic replay) must be present"
    assert "run_synthetic_guardrail_block" in run_ids, "run_synthetic_guardrail_block must be present"


def test_2_status_filters():
    """2. Status filters work accurately."""
    runs = fetch_runs(api_url="http://offline-backend:9999")

    done_runs = [r for r in runs if str(r.get("status", "")).lower() == "done"]
    assert len(done_runs) >= 2, "Expected at least 2 done runs"

    needs_human_runs = [r for r in runs if str(r.get("status", "")).lower() == "needs_human"]
    assert len(needs_human_runs) >= 2, "Expected at least 2 needs_human runs (0f198f159c51, 90c69b2170f4)"

    blocked_runs = [r for r in runs if str(r.get("status", "")).lower() == "blocked"]
    assert len(blocked_runs) >= 1, "Expected at least 1 blocked run (run_synthetic_guardrail_block)"

    # Mode filters
    live_runs = [r for r in runs if str(r.get("execution_mode", "")).upper() == "LIVE"]
    assert len(live_runs) >= 3, "Expected at least 3 genuine LIVE runs"

    synthetic_runs = [r for r in runs if str(r.get("execution_mode", "")).upper() in ("SYNTHETIC", "SYNTHETIC_GUARDRAIL_SAMPLE")]
    assert len(synthetic_runs) >= 1, "Expected at least 1 synthetic run"


def test_3_search_functionality():
    """3. Search by run ID and requirement works."""
    runs = fetch_runs(api_url="http://offline-backend:9999")

    # Search by ID
    id_results = [r for r in runs if "0f04e14907d7" in r.get("run_id", "").lower()]
    assert len(id_results) == 1
    assert id_results[0]["run_id"] == "0f04e14907d7"

    # Search by requirement keyword
    kw_results = [r for r in runs if "search_notes_by_title" in r.get("requirement", "").lower()]
    assert len(kw_results) >= 1, "Expected search for search_notes_by_title to return matching runs"


def test_4_detailed_run_inspection():
    """4. Detailed run inspection works across all curated runs without crashes."""
    runs = fetch_runs(api_url="http://offline-backend:9999")
    for r in runs:
        rid = r["run_id"]
        detail = fetch_run_details(rid, api_url="http://offline-backend:9999")
        assert detail is not None, f"Failed to fetch details for {rid}"
        assert detail.get("run_id") == rid

        events = fetch_run_events(rid, offset=0, limit=100, api_url="http://offline-backend:9999")
        stages = evaluate_all_stages(detail, events)
        assert len(stages) == 11, f"Run {rid} must produce 11 pipeline stages"

        html_code = generate_pipeline_html(stages, detail)
        assert len(html_code) > 100, f"HTML generation failed for {rid}"


def test_5_missing_artifacts_fail_gracefully():
    """5. Missing or incomplete artifacts fail gracefully."""
    # Non-existent run
    detail = fetch_run_details("non_existent_run_99999", api_url="http://offline-backend:9999")
    assert detail is None

    # Empty events
    events = fetch_run_events("non_existent_run_99999", api_url="http://offline-backend:9999")
    assert events == []

    # Partial/empty dict
    stages = evaluate_all_stages({}, [])
    assert len(stages) == 11
    html_code = generate_pipeline_html(stages, {})
    assert "workflow-canvas" in html_code


def test_6_secret_sanitization():
    """6. Secret sanitization tests pass: no private tokens or Windows paths."""
    showcase_dir = Path("showcase_data")
    forbidden_patterns = [
        re.compile(r"ghp_[A-Za-z0-9_]{10,}", re.IGNORECASE),
        re.compile(r"github_pat_[A-Za-z0-9_]{20,}", re.IGNORECASE),
        re.compile(r"sk-[A-Za-z0-9_-]{15,}", re.IGNORECASE),
        re.compile(r"gsk_[A-Za-z0-9_-]{15,}", re.IGNORECASE),
        re.compile(r"C:\\Users\\Kushagra", re.IGNORECASE),
        re.compile(r"D:\\Agentic AI", re.IGNORECASE),
    ]

    for p in showcase_dir.rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="ignore")
            for pattern in forbidden_patterns:
                match = pattern.search(text)
                assert match is None, f"Sanitization failed: found {match.group(0)} in {p}"


def test_7_successful_run_representation():
    """7. Existing successful run 0f04e14907d7 remains correctly represented."""
    detail = fetch_run_details("0f04e14907d7", api_url="http://offline-backend:9999")
    assert detail is not None
    assert detail["status"] == "done"
    assert detail.get("execution_mode") == "LIVE"

    # Reviewer approval
    review = detail.get("review") or {}
    assert review.get("verdict") == "approve"

    # Docker test reports: 13 passed tests
    test_reports = detail.get("test_reports") or []
    assert len(test_reports) > 0
    last_report = test_reports[-1]
    assert last_report.get("passed") is True
    assert last_report.get("exit_code") == 0
    assert "13 passed" in (last_report.get("raw_output") or "")

    # Workspace hash recorded
    assert last_report.get("workspace_hash") == "29a35b3efca07f7e4694636c9ae4b736e2b67736d4ff7d6c19effa698d290fae"


def test_8_pr_7_link_resolves():
    """8. PR #7 link resolves correctly."""
    detail = fetch_run_details("0f04e14907d7", api_url="http://offline-backend:9999")
    assert detail is not None
    pr = detail.get("pr") or {}
    assert pr.get("pull_number") == 7
    assert pr.get("url") == "https://github.com/Kushagra-Pachauri/demo_repo/pull/7"
    assert "Kushagra-Pachauri/demo_repo" in pr.get("url", "")


def test_9_public_showcase_mode_intact():
    """9. Public Showcase Mode continues working without Docker, Ollama or FastAPI."""
    health = fetch_health(api_url="http://offline-backend:9999")
    assert health is not None
    assert health["mode"] == "public_showcase"
    assert health["status"] == "healthy"
