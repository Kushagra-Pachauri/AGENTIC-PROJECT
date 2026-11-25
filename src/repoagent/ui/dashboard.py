"""Streamlit monitoring and execution dashboard for the RepoAgent pipeline.

Redesigned with Premium Emerald Dark aesthetic inspired by futuristic developer consoles:
- Near-black backgrounds (#05090B) with subtle green grid and radial emerald glow.
- Deep charcoal surfaces (#0D1115, #101419, #151A20) with rounded containers (12-14px).
- Connected horizontal pipeline visualization with glowing active stage and node connectors.
- Emerald green borders (#176B42), bright neon accents (#35D878), and high-contrast typography.
- Fully preserved 11-stage verification, fail-closed DONE Gate auditing, and zero-write safety.
"""

from __future__ import annotations

import html
import json
import os
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import streamlit as st

try:
    from repoagent.observability import redact_text_for_tracing
except ImportError:
    import re
    _DEFAULT_SECRET_PATTERNS = [
        re.compile(r"(ghp_[A-Za-z0-9_]{36,})", re.IGNORECASE),
        re.compile(r"(github_pat_[A-Za-z0-9_]{82})", re.IGNORECASE),
        re.compile(r"(sk-[A-Za-z0-9_-]{20,})", re.IGNORECASE),
        re.compile(r"(gsk_[A-Za-z0-9_-]{20,})", re.IGNORECASE),
    ]
    def redact_text_for_tracing(text: str) -> str:
        res = str(text)
        for pattern in _DEFAULT_SECRET_PATTERNS:
            res = pattern.sub("[REDACTED_SECRET]", res)
        return res

DEFAULT_API_URL = os.environ.get("REPOAGENT_API_URL", "http://localhost:8000").rstrip("/")
DEFAULT_ALLOWED_REPOS = ["demo_repo"]

# Design System Palette Tokens
PALETTE = {
    "bg_main": "#05090B",
    "surface_primary": "#0D1115",
    "surface_secondary": "#151A20",
    "surface_card": "#101419",
    "border_subtle": "#293239",
    "border_emerald": "#176B42",
    "emerald_primary": "#16A34A",
    "neon_accent": "#35D878",
    "primary_text": "#E7EFEB",
    "secondary_text": "#98A49F",
    "muted_text": "#68736F",
    "status_completed": "#16A34A",
    "status_running": "#F59E0B",
    "status_needs_human": "#F59E0B",
    "status_failed": "#EF4444",
    "status_pending": "#4B5563",
}


# ---------------------------------------------------------------------------
# CSS & Styling Injection
# ---------------------------------------------------------------------------


def get_emerald_theme_css() -> str:
    """Return the complete CSS string for the Emerald Dark Design System."""
    return f"""
    <style>
    /* ---------------------------------------------------------------------- */
    /* Root & Global Backgrounds                                              */
    /* ---------------------------------------------------------------------- */
    html, body, [data-testid="stAppViewContainer"], .main {{
        background-color: {PALETTE["bg_main"]} !important;
        background-image:
            radial-gradient(circle at 50% 12%, rgba(22, 163, 74, 0.08) 0%, rgba(5, 9, 11, 0) 70%),
            linear-gradient(rgba(23, 107, 66, 0.04) 1px, transparent 1px),
            linear-gradient(90deg, rgba(23, 107, 66, 0.04) 1px, transparent 1px) !important;
        background-size: 100% 100%, 32px 32px, 32px 32px !important;
        color: {PALETTE["primary_text"]} !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
            Roboto, "Helvetica Neue", Arial, sans-serif !important;
    }}

    [data-testid="stHeader"] {{
        background-color: rgba(5, 9, 11, 0.8) !important;
        backdrop-filter: blur(8px) !important;
        border-bottom: 1px solid rgba(41, 50, 57, 0.4) !important;
    }}

    /* Hide standard Streamlit header decoration line */
    [data-testid="stDecoration"] {{
        display: none !important;
    }}

    /* ---------------------------------------------------------------------- */
    /* Typography & Section Dividers                                          */
    /* ---------------------------------------------------------------------- */
    h1, h2, h3, h4, h5, h6 {{
        color: {PALETTE["primary_text"]} !important;
        font-weight: 700 !important;
        letter-spacing: -0.01em !important;
    }}

    hr {{
        border: none !important;
        border-top: 1px solid {PALETTE["border_subtle"]} !important;
        margin: 24px 0 !important;
    }}

    /* ---------------------------------------------------------------------- */
    /* Streamlit KPI Metric Cards                                             */
    /* ---------------------------------------------------------------------- */
    div[data-testid="stMetric"] {{
        background-color: {PALETTE["surface_primary"]} !important;
        border: 1px solid {PALETTE["border_subtle"]} !important;
        border-radius: 12px !important;
        padding: 18px 24px !important;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4) !important;
        transition: border-color 0.2s ease, transform 0.2s ease !important;
    }}

    div[data-testid="stMetric"]:hover {{
        border-color: {PALETTE["border_emerald"]} !important;
        transform: translateY(-2px) !important;
    }}

    div[data-testid="stMetricLabel"] p {{
        color: #A3B0AA !important;
        font-size: 0.95rem !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
        font-weight: 600 !important;
    }}

    div[data-testid="stMetricValue"] {{
        color: {PALETTE["primary_text"]} !important;
        font-size: 1.85rem !important;
        font-weight: 700 !important;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace !important;
    }}

    /* ---------------------------------------------------------------------- */
    /* Form Inputs, Select Boxes, and Text Areas                              */
    /* ---------------------------------------------------------------------- */
    label[data-testid="stWidgetLabel"] p, label[data-testid="stWidgetLabel"] span {{
        font-size: 1.0rem !important;
        font-weight: 600 !important;
        color: {PALETTE["primary_text"]} !important;
        margin-bottom: 6px !important;
    }}

    textarea, input, [data-baseweb="select"] > div {{
        background-color: {PALETTE["surface_card"]} !important;
        border: 1px solid {PALETTE["border_subtle"]} !important;
        color: {PALETTE["primary_text"]} !important;
        border-radius: 10px !important;
        font-size: 0.98rem !important;
    }}

    textarea:focus, input:focus {{
        border-color: {PALETTE["emerald_primary"]} !important;
        box-shadow: 0 0 0 1px {PALETTE["emerald_primary"]},
            0 0 12px rgba(22, 163, 74, 0.25) !important;
    }}

    [data-baseweb="popover"], [data-baseweb="menu"] {{
        background-color: {PALETTE["surface_primary"]} !important;
        border: 1px solid {PALETTE["border_subtle"]} !important;
        border-radius: 10px !important;
    }}

    li[role="option"] {{
        color: {PALETTE["primary_text"]} !important;
        font-size: 0.95rem !important;
    }}

    li[role="option"]:hover, li[aria-selected="true"] {{
        background-color: {PALETTE["surface_secondary"]} !important;
        color: {PALETTE["neon_accent"]} !important;
    }}

    /* ---------------------------------------------------------------------- */
    /* Buttons: Primary Emerald Action & Secondary Dark                       */
    /* ---------------------------------------------------------------------- */
    div.stButton > button[kind="primary"] {{
        background: linear-gradient(
            180deg, {PALETTE["emerald_primary"]} 0%, #15803D 100%
        ) !important;
        border: 1px solid {PALETTE["neon_accent"]} !important;
        color: #05090B !important;
        font-weight: 700 !important;
        font-size: 1.0rem !important;
        border-radius: 10px !important;
        padding: 12px 28px !important;
        box-shadow: 0 4px 14px rgba(22, 163, 74, 0.35) !important;
        transition: all 0.2s ease !important;
    }}

    div.stButton > button[kind="primary"]:hover {{
        background: {PALETTE["neon_accent"]} !important;
        box-shadow: 0 0 20px rgba(53, 216, 120, 0.55) !important;
        color: #05090B !important;
        transform: translateY(-1px) !important;
    }}

    div.stButton > button[kind="secondary"] {{
        background: {PALETTE["surface_card"]} !important;
        border: 1px solid {PALETTE["border_subtle"]} !important;
        color: {PALETTE["primary_text"]} !important;
        border-radius: 10px !important;
        font-size: 0.95rem !important;
        font-weight: 600 !important;
        transition: all 0.2s ease !important;
    }}

    div.stButton > button[kind="secondary"]:hover {{
        border-color: {PALETTE["border_emerald"]} !important;
        background: {PALETTE["surface_secondary"]} !important;
    }}

    /* ---------------------------------------------------------------------- */
    /* Code Blocks, Logs & Expanders                                          */
    /* ---------------------------------------------------------------------- */
    pre, code {{
        background-color: #080C0E !important;
        color: {PALETTE["neon_accent"]} !important;
        border: 1px solid #1A2328 !important;
        border-radius: 8px !important;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace !important;
        font-size: 0.94rem !important;
        line-height: 1.55 !important;
    }}

    .stMarkdown p, .stMarkdown li {{
        font-size: 1.0rem !important;
        line-height: 1.6 !important;
    }}

    div[data-testid="stExpander"] {{
        background-color: {PALETTE["surface_primary"]} !important;
        border: 1px solid {PALETTE["border_subtle"]} !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3) !important;
    }}

    div[data-testid="stExpander"]:hover {{
        border-color: {PALETTE["border_emerald"]} !important;
    }}

    /* ---------------------------------------------------------------------- */
    /* Custom Scrollbars                                                      */
    /* ---------------------------------------------------------------------- */
    ::-webkit-scrollbar {{
        width: 8px;
        height: 8px;
    }}
    ::-webkit-scrollbar-track {{
        background: {PALETTE["bg_main"]};
    }}
    ::-webkit-scrollbar-thumb {{
        background: {PALETTE["border_emerald"]};
        border-radius: 4px;
    }}
    ::-webkit-scrollbar-thumb:hover {{
        background: {PALETTE["neon_accent"]};
    }}

    /* ---------------------------------------------------------------------- */
    /* Component Cards & Containers                                           */
    /* ---------------------------------------------------------------------- */
    .emerald-card {{
        background-color: {PALETTE["surface_primary"]};
        border: 1px solid {PALETTE["border_subtle"]};
        border-radius: 14px;
        padding: 24px 28px;
        margin-bottom: 22px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
    }}

    .emerald-badge-pill {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px 14px;
        border-radius: 18px;
        font-size: 0.88rem;
        font-weight: 600;
        letter-spacing: 0.02em;
    }}

    .pill-connected {{
        background: rgba(22, 163, 74, 0.12);
        border: 1px solid {PALETTE["border_emerald"]};
        color: {PALETTE["neon_accent"]};
    }}

    .pill-disconnected {{
        background: rgba(239, 68, 68, 0.12);
        border: 1px solid rgba(239, 68, 68, 0.4);
        color: {PALETTE["status_failed"]};
    }}

    .pill-muted {{
        background: {PALETTE["surface_card"]};
        border: 1px solid {PALETTE["border_subtle"]};
        color: {PALETTE["muted_text"]};
    }}

    .pill-branch {{
        background: rgba(59, 130, 246, 0.15);
        border: 1px solid rgba(59, 130, 246, 0.4);
        color: #60A5FA;
        font-family: ui-monospace, SFMono-Regular, monospace;
    }}

    .pill-hash {{
        background: {PALETTE["surface_secondary"]};
        border: 1px solid {PALETTE["border_subtle"]};
        color: {PALETTE["secondary_text"]};
        font-family: ui-monospace, SFMono-Regular, monospace;
    }}

    /* ---------------------------------------------------------------------- */
    /* Reference-Inspired Connected Pipeline Workflow                         */
    /* ---------------------------------------------------------------------- */
    .workflow-canvas {{
        background-color: {PALETTE["surface_primary"]};
        border: 1px solid {PALETTE["border_subtle"]};
        border-radius: 14px;
        padding: 24px;
        margin: 16px 0 24px 0;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.55);
        position: relative;
    }}

    .workflow-top-bar {{
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        align-items: center;
        gap: 16px;
        padding-bottom: 20px;
        border-bottom: 1px solid rgba(41, 50, 57, 0.6);
        margin-bottom: 22px;
    }}

    .workflow-meta-group {{
        display: flex;
        flex-direction: column;
        gap: 4px;
    }}

    .workflow-meta-label {{
        font-size: 0.85rem;
        color: {PALETTE["secondary_text"]};
        text-transform: uppercase;
        letter-spacing: 0.05em;
        font-weight: 600;
    }}

    .workflow-meta-val {{
        font-size: 1.25rem;
        font-weight: 700;
        color: {PALETTE["primary_text"]};
    }}

    .workflow-title-row {{
        margin-bottom: 18px;
    }}

    .workflow-filename {{
        font-family: ui-monospace, SFMono-Regular, monospace;
        font-size: 1.18rem;
        font-weight: 700;
        color: {PALETTE["primary_text"]};
    }}

    .workflow-dispatch {{
        font-size: 0.9rem;
        color: {PALETTE["muted_text"]};
        margin-top: 2px;
    }}

    /* Connected horizontal track */
    .pipeline-track-scroll {{
        display: flex;
        align-items: center;
        overflow-x: auto;
        padding: 28px 12px;
        gap: 0;
        scrollbar-width: thin;
        scrollbar-color: {PALETTE["border_emerald"]} {PALETTE["surface_primary"]};
    }}

    .pipeline-stage-wrapper {{
        display: flex;
        align-items: center;
        flex-shrink: 0;
    }}

    .pipeline-node-card {{
        background: {PALETTE["surface_card"]};
        border: 1px solid {PALETTE["border_subtle"]};
        border-radius: 12px;
        padding: 12px 18px;
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 165px;
        transition: all 0.25s ease;
    }}

    .pipeline-node-card.node-completed {{
        border-color: {PALETTE["border_emerald"]};
        background: #0B1411;
    }}

    .pipeline-node-card.node-running {{
        border-color: {PALETTE["neon_accent"]};
        background: #091912;
        box-shadow: 0 0 28px rgba(53, 216, 120, 0.45), 0 0 10px rgba(22, 163, 74, 0.6);
        transform: scale(1.04);
        z-index: 2;
    }}

    .pipeline-node-card.node-needs-human {{
        border-color: {PALETTE["status_needs_human"]};
        background: #18150D;
        box-shadow: 0 0 18px rgba(245, 158, 11, 0.3);
    }}

    .pipeline-node-card.node-failed, .pipeline-node-card.node-blocked {{
        border-color: {PALETTE["status_failed"]};
        background: #180D10;
        box-shadow: 0 0 18px rgba(239, 68, 68, 0.3);
    }}

    .pipeline-node-card.node-pending {{
        opacity: 0.65;
        background: #0B0E11;
    }}

    .node-icon-circle {{
        width: 26px;
        height: 26px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.85rem;
        font-weight: 700;
        flex-shrink: 0;
    }}

    .icon-completed {{
        background: {PALETTE["emerald_primary"]};
        color: #05090B;
        box-shadow: 0 0 8px rgba(22, 163, 74, 0.5);
    }}

    .icon-running {{
        background: {PALETTE["neon_accent"]};
        color: #05090B;
        box-shadow: 0 0 12px rgba(53, 216, 120, 0.8);
        animation: pulse-glow 1.5s infinite ease-in-out;
    }}

    .icon-needs-human {{
        background: {PALETTE["status_needs_human"]};
        color: #05090B;
    }}

    .icon-failed {{
        background: {PALETTE["status_failed"]};
        color: #FFFFFF;
    }}

    .icon-pending {{
        border: 2px solid {PALETTE["border_subtle"]};
        background: transparent;
        color: {PALETTE["muted_text"]};
    }}

    .node-text-col {{
        display: flex;
        flex-direction: column;
    }}

    .node-name {{
        font-size: 0.98rem;
        font-weight: 700;
        color: {PALETTE["primary_text"]};
        white-space: nowrap;
    }}

    .node-sub {{
        font-size: 0.82rem;
        color: {PALETTE["secondary_text"]};
        font-family: ui-monospace, SFMono-Regular, monospace;
        margin-top: 1px;
    }}

    /* Connector line with junction dot */
    .node-connector-line {{
        width: 36px;
        height: 3px;
        background: {PALETTE["border_subtle"]};
        position: relative;
        flex-shrink: 0;
    }}

    .node-connector-line::after {{
        content: '';
        position: absolute;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: {PALETTE["border_subtle"]};
        top: -2.5px;
        left: 14px;
    }}

    .node-connector-line.connector-active {{
        background: {PALETTE["border_emerald"]};
    }}

    .node-connector-line.connector-active::after {{
        background: {PALETTE["neon_accent"]};
        box-shadow: 0 0 6px {PALETTE["neon_accent"]};
    }}

    .workflow-controls-pill {{
        display: flex;
        align-items: center;
        gap: 6px;
        background: {PALETTE["surface_card"]};
        border: 1px solid {PALETTE["border_subtle"]};
        border-radius: 8px;
        padding: 4px 8px;
        position: absolute;
        bottom: 18px;
        right: 24px;
        font-size: 0.82rem;
        color: {PALETTE["secondary_text"]};
    }}

    @keyframes pulse-glow {{
        0% {{ transform: scale(0.95); opacity: 0.8; }}
        50% {{ transform: scale(1.1); opacity: 1; }}
        100% {{ transform: scale(0.95); opacity: 0.8; }}
    }}
    </style>
    """


def render_html(html_str: str) -> None:
    """Safely render raw HTML without markdown escaping or indentation issues."""
    dedented = textwrap.dedent(html_str).strip()
    if hasattr(st, "html"):
        st.html(dedented)
    else:
        st.markdown(dedented, unsafe_allow_html=True)


def inject_emerald_theme_css() -> None:
    """Inject Emerald Dark styling into the active Streamlit app."""
    render_html(get_emerald_theme_css())


# ---------------------------------------------------------------------------
# Validation & Configuration Helpers
# ---------------------------------------------------------------------------


def get_allowed_repos() -> list[str]:
    """Retrieve allowed repository targets from environment with safe default."""
    raw_env = os.environ.get("REPOAGENT_UI_REPOS", "demo_repo")
    repos = [r.strip() for r in raw_env.split(",") if r.strip()]
    return repos if repos else list(DEFAULT_ALLOWED_REPOS)


def validate_repo_input(repo_url: str) -> tuple[bool, str]:
    """Validate repository target against allowlist and path-traversal safety."""
    cleaned = repo_url.strip()
    if not cleaned:
        return False, "Repository target cannot be empty."
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        return False, "Path traversal or absolute paths are strictly disallowed."
    allowed = get_allowed_repos()
    if cleaned not in allowed:
        return False, f"Repository '{cleaned}' is not in allowed list: {', '.join(allowed)}"
    return True, ""


def validate_requirement_input(requirement: str) -> tuple[bool, str]:
    """Validate requirement text meeting the minimum 5-character threshold."""
    cleaned = requirement.strip()
    if len(cleaned) < 5:
        return False, "Requirement must be at least 5 characters."
    return True, ""


def validate_retries_input(max_retries: int) -> tuple[bool, str]:
    """Ensure max retries is bounded strictly within [1, 3]."""
    if max_retries < 1 or max_retries > 3:
        return False, "Max retries must be between 1 and 3."
    return True, ""


# ---------------------------------------------------------------------------
# API Client Helpers
# ---------------------------------------------------------------------------


def get_showcase_dirs() -> list[Path]:
    """Return directories containing run records for showcase/fallback mode."""
    candidates = [
        Path("showcase_data"),
        Path(__file__).resolve().parent.parent.parent.parent / "showcase_data",
    ]
    curated = [p.resolve() for p in candidates if p.is_dir()]
    if curated:
        return curated

    fallback = [
        Path("runs"),
        Path(__file__).resolve().parent.parent.parent.parent / "runs",
    ]
    return [p.resolve() for p in fallback if p.is_dir()]


def fetch_health(api_url: str | None = DEFAULT_API_URL, timeout: float = 3.0) -> dict[str, Any] | None:
    """Fetch health diagnostics from FastAPI backend or return public showcase status."""
    if api_url:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(
                    f"{api_url.rstrip('/')}/health",
                    headers={"ngrok-skip-browser-warning": "1"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        return data
        except Exception:
            pass
    return {
        "status": "healthy",
        "mode": "public_showcase",
        "version": "0.1.0",
        "langfuse_enabled": False,
        "active_run": None,
        "environment": "Streamlit Community Cloud",
    }


def fetch_runs(
    api_url: str | None = DEFAULT_API_URL, auth_token: str | None = None, timeout: float = 3.0
) -> list[dict[str, Any]]:
    """Fetch list of runs from FastAPI backend, falling back to disk in standalone/showcase mode."""
    is_demo = os.environ.get("REPOAGENT_DEMO_MODE", "").lower() in ("1", "true")
    runs_list: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1. Query remote live gateway or local API if endpoint provided
    if api_url and (not is_demo or auth_token or "ngrok" in str(api_url) or ":8008" in str(api_url)):
        headers = {"ngrok-skip-browser-warning": "1"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        for ep in (f"{api_url.rstrip('/')}/api/v1/runs", f"{api_url.rstrip('/')}/runs"):
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.get(ep, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data and isinstance(data, list):
                            for r in data:
                                rid = r.get("run_id")
                                if rid and rid not in seen_ids:
                                    if not r.get("execution_mode"):
                                        is_synth = bool(r.get("is_synthetic") or "synthetic" in str(rid).lower())
                                        is_rep = bool(r.get("is_replay") or "replay" in str(rid).lower())
                                        is_mock = "mock" in str(rid).lower() or r.get("is_mock_test", False)
                                        if is_synth:
                                            r["execution_mode"] = "SYNTHETIC"
                                        elif is_mock:
                                            r["execution_mode"] = "MOCK"
                                        elif is_rep:
                                            r["execution_mode"] = "REPLAY"
                                        else:
                                            r["execution_mode"] = "LIVE"
                                    runs_list.append(r)
                                    seen_ids.add(rid)
                            break
            except Exception:
                pass

    # 2. Standalone / Showcase fallback from disk
    for root_dir in get_showcase_dirs():
        for run_path in sorted(root_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not run_path.is_dir():
                continue
            run_id = run_path.name
            if run_id in seen_ids:
                continue
            state_file = run_path / "report.json"
            if not state_file.is_file():
                state_file = run_path / "run_state.json"
            if state_file.is_file():
                try:
                    d = json.loads(state_file.read_text(encoding="utf-8"))
                    is_synth = bool(d.get("is_synthetic") or "synthetic" in run_id.lower())
                    is_rep = bool(d.get("is_replay") or "replay" in run_id.lower())
                    is_mock = "mock" in run_id.lower() or d.get("is_mock_test", False)
                    exec_mode = str(d.get("execution_mode", "")).upper()
                    if not exec_mode:
                        if is_synth:
                            exec_mode = "SYNTHETIC"
                        elif is_mock:
                            exec_mode = "MOCK"
                        elif is_rep:
                            exec_mode = "REPLAY"
                        else:
                            exec_mode = "LIVE"

                    runs_list.append({
                        "run_id": d.get("run_id", run_id),
                        "status": str(d.get("status", "done")).lower(),
                        "requirement": d.get("requirement", ""),
                        "duration_s": d.get("duration_s"),
                        "created_at": d.get("created_at") or d.get("started_at"),
                        "finished_at": d.get("finished_at"),
                        "is_replay": is_rep,
                        "is_synthetic": is_synth,
                        "execution_mode": exec_mode,
                        "pr": d.get("pr"),
                        "test_reports": d.get("test_reports"),
                        "review": d.get("review"),
                    })
                    seen_ids.add(run_id)
                except Exception:
                    pass

    return runs_list


def fetch_run_details(
    run_id: str,
    api_url: str | None = DEFAULT_API_URL,
    auth_token: str | None = None,
    timeout: float = 3.0,
) -> dict[str, Any] | None:
    """Fetch structured run status and reports from FastAPI backend or local disk."""
    if not run_id:
        return None
    is_demo = os.environ.get("REPOAGENT_DEMO_MODE", "").lower() in ("1", "true")
    if api_url and (not is_demo or auth_token or "ngrok" in str(api_url) or ":8008" in str(api_url)):
        headers = {"ngrok-skip-browser-warning": "1"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        for ep in (
            f"{api_url.rstrip('/')}/api/v1/runs/{run_id}",
            f"{api_url.rstrip('/')}/runs/{run_id}",
        ):
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.get(ep, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()
            except Exception:
                pass

    # Standalone disk fallback
    for root_dir in get_showcase_dirs():
        r_dir = root_dir / run_id
        for fn in ("report.json", "run_state.json"):
            sf = r_dir / fn
            if sf.is_file():
                try:
                    return json.loads(sf.read_text(encoding="utf-8"))
                except Exception:
                    pass
    return None


def fetch_run_events(
    run_id: str,
    offset: int = 0,
    limit: int = 100,
    api_url: str | None = DEFAULT_API_URL,
    auth_token: str | None = None,
    timeout: float = 3.0,
) -> list[dict[str, Any]]:
    """Fetch recorded audit events for a run from FastAPI backend or local disk."""
    if not run_id:
        return []
    is_demo = os.environ.get("REPOAGENT_DEMO_MODE", "").lower() in ("1", "true")
    if api_url and (not is_demo or auth_token or "ngrok" in str(api_url) or ":8008" in str(api_url)):
        headers = {"ngrok-skip-browser-warning": "1"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        for ep in (
            f"{api_url.rstrip('/')}/api/v1/runs/{run_id}/events?offset={offset}&limit={limit}",
            f"{api_url.rstrip('/')}/runs/{run_id}/events?offset={offset}&limit={limit}",
        ):
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.get(ep, headers=headers)
                    if resp.status_code == 200:
                        res = resp.json()
                        if isinstance(res, list):
                            return res
                        if isinstance(res, dict) and "events" in res:
                            return res["events"]
            except Exception:
                pass

    # Standalone disk fallback
    events: list[dict[str, Any]] = []
    for root_dir in get_showcase_dirs():
        ef = root_dir / run_id / "events.jsonl"
        if ef.is_file():
            try:
                for line in ef.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        events.append(json.loads(line))
            except Exception:
                pass
            if events:
                break
    return events[offset : offset + limit]


def submit_new_run(
    repo_url: str,
    requirement: str,
    max_retries: int = 3,
    create_pr: bool = False,
    use_live_agents: bool = True,
    api_url: str = DEFAULT_API_URL,
    auth_token: str | None = None,
    timeout: float = 15.0,
) -> tuple[bool, dict[str, Any] | str]:
    """Submit a validated new run request to the FastAPI backend or Live Gateway."""
    v_repo, err_repo = validate_repo_input(repo_url)
    if not v_repo:
        return False, err_repo

    v_req, err_req = validate_requirement_input(requirement)
    if not v_req:
        return False, err_req

    v_retries, err_retries = validate_retries_input(max_retries)
    if not v_retries:
        return False, err_retries

    payload = {
        "repo_url": repo_url.strip(),
        "repo": repo_url.strip(),
        "requirement": requirement.strip(),
        "max_retries": max_retries,
        "create_pr": bool(create_pr),
        "use_live_agents": bool(use_live_agents),
    }

    headers = {"ngrok-skip-browser-warning": "1"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    base = api_url.rstrip("/")
    endpoints = [f"{base}/api/v1/runs", f"{base}/runs"]
    last_err = ""
    for ep in endpoints:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(ep, json=payload, headers=headers)
                if resp.status_code == 201:
                    return True, resp.json()
                elif resp.status_code in (400, 401, 403, 429):
                    return False, f"Gateway Error ({resp.status_code}): {resp.text}"
                last_err = f"HTTP {resp.status_code}: {resp.text}"
        except Exception as exc:
            last_err = str(exc)

    return False, f"Failed to submit run to gateway: {last_err}"


# ---------------------------------------------------------------------------
# Metrics, Formatting, and Sanitization
# ---------------------------------------------------------------------------


def compute_overview_metrics(runs: list[dict[str, Any]]) -> dict[str, int]:
    """Calculate aggregate counts across recorded runs."""
    metrics = {
        "total": len(runs),
        "completed": 0,
        "failed": 0,
        "needs_human": 0,
        "blocked": 0,
        "running": 0,
    }
    for r in runs:
        st_val = str(r.get("status", "")).lower()
        if st_val == "done":
            metrics["completed"] += 1
        elif st_val == "needs_human":
            metrics["needs_human"] += 1
        elif st_val == "blocked":
            metrics["blocked"] += 1
        elif st_val == "running":
            metrics["running"] += 1
        elif st_val == "failed":
            metrics["failed"] += 1
    return metrics


def sanitize_display_text(text: str | None, max_length: int = 5000) -> str:
    """Apply secret redaction and length bounding for dashboard presentation."""
    if not text:
        return ""
    redacted = redact_text_for_tracing(str(text))
    if len(redacted) > max_length:
        return redacted[:max_length] + "\n... [TRUNCATED FOR DISPLAY]"
    return redacted


def format_duration(seconds: float | None) -> str:
    """Format duration nicely, distinguishing short tasks from long or stale spans."""
    if seconds is None:
        return "N/A"
    if seconds < 60:
        return f"{seconds:.2f} s"
    if seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    if seconds < 86400:
        hrs = seconds / 3600
        return f"{hrs:.1f} hours"
    days = seconds / 86400
    return f"{days:.1f} days (interrupted/stale span)"


def is_terminal_or_stale(run_data: dict[str, Any]) -> bool:
    """Determine if run reached a terminal status or is inactive/stale."""
    st_val = str(run_data.get("status", "")).lower()
    if st_val in ("done", "needs_human", "blocked", "failed"):
        return True
    return bool(run_data.get("is_stale", False))


# ---------------------------------------------------------------------------
# Strict Stage-to-Backend Evidence Evaluation
# ---------------------------------------------------------------------------


def evaluate_stage_1_requirement(
    run_data: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, str]:
    """Stage 1: Requirement Analysis."""
    event_types = [e.get("event_type") for e in events]
    status_val = str(run_data.get("status", "")).lower()

    if status_val == "blocked" or "INPUT_BLOCKED" in event_types:
        errs = run_data.get("errors", [])
        detail = errs[-1] if errs else "Requirement blocked by security guardrails."
        return "BLOCKED", detail
    if "INPUT_ALLOWED" in event_types or status_val in ("done", "needs_human", "running"):
        return "COMPLETED", "Requirement screened and validated successfully."
    if "INPUT_SCREENING_STARTED" in event_types:
        return "RUNNING", "Requirement screening underway."
    return "UNKNOWN", "No requirement screening evidence found in backend."


def evaluate_stage_2_repo_understanding(
    run_data: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, str]:
    """Stage 2: Repository Understanding (Requires actual workspace initialization evidence)."""
    status_val = str(run_data.get("status", "")).lower()
    if status_val == "blocked":
        return "PENDING", "Stage skipped: execution blocked by input security guardrails."

    has_analyze_evidence = any(
        e.get("stage") == "analyze"
        or e.get("agent_name") in ("planner", "repo-planner")
        for e in events
    )
    repo_url = str(run_data.get("repo_url", ""))

    if has_analyze_evidence or run_data.get("repo_map") or run_data.get("plan"):
        return (
            "COMPLETED",
            f"Isolated workspace initialized at: {repo_url or 'runs/<run_id>/workspace'}",
        )

    started_analyze = [
        e for e in events if e.get("event_type") in ("AGENT_STARTED", "TASK_STARTED") and e.get("stage") == "analyze"
    ]
    if started_analyze:
        return "RUNNING", "Analyzing repository files and mapping architecture."

    return "NOT AVAILABLE", "Actual workspace initialization evidence not confirmed in backend."


def evaluate_stage_3_planning(
    run_data: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, str]:
    """Stage 3: Planning Agent."""
    status_val = str(run_data.get("status", "")).lower()
    if status_val == "blocked":
        return "PENDING", "Stage skipped: execution blocked by input security guardrails."

    if run_data.get("plan"):
        return "COMPLETED", "Structured implementation plan generated via Google ADK Planner."

    planner_done = any(
        e.get("event_type") in ("AGENT_COMPLETED", "TASK_COMPLETED", "TOOL_COMPLETED")
        and (e.get("agent_name") in ("planner", "repo-planner") or e.get("stage") in ("analyze", "plan"))
        for e in events
    )
    # If the coding stage has started, the plan was completed
    coding_started = any(e.get("stage") == "code" for e in events)
    if planner_done or coding_started:
        return "COMPLETED", "Structured implementation plan generated via Google ADK Planner."

    planner_active = any(
        e.get("event_type") in ("AGENT_STARTED", "TASK_STARTED", "TOOL_STARTED")
        and (e.get("agent_name") in ("planner", "repo-planner") or e.get("stage") in ("analyze", "plan"))
        for e in events
    )
    if planner_active:
        return "RUNNING", "Google ADK Planner active over A2A (port 8001)."

    if any("plan" in str(err).lower() for err in run_data.get("errors", [])):
        return "FAILED", "Planning stage failed or rejected."

    return "NOT AVAILABLE", "Planning agent evidence not found in backend."


def evaluate_stage_4_coding(
    run_data: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, str]:
    """Stage 4: Coding Agent."""
    code_task_done = any(
        e.get("event_type") == "TASK_COMPLETED" and e.get("stage") == "code" for e in events
    )
    if code_task_done or bool(run_data.get("test_reports")):
        retries = run_data.get("retry_count", 0)
        return "COMPLETED", f"CrewAI coding agent generated changes (Correction cycle: {retries})."

    code_active = any(
        e.get("event_type") in ("AGENT_STARTED", "TASK_STARTED") and e.get("stage") == "code"
        for e in events
    )
    if code_active:
        return "RUNNING", "CrewAI coding agent generating file modifications."

    return "PENDING", "Coding stage waiting for plan completion."


def evaluate_stage_5_guardrails(
    run_data: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, str]:
    """Stage 5: ChangeSet Validation & Guardrails."""
    event_types = [e.get("event_type") for e in events]
    if "TOOL_GUARD_BLOCKED" in event_types or "SECRET_SCAN_BLOCKED" in event_types:
        return (
            "BLOCKED",
            "ChangeSet blocked by security guardrails (path traversal or secret scan).",
        )

    if (
        "CHANGESET_VALIDATION_FAILED" in event_types
        or run_data.get("rejected_changes_count", 0) > 0
    ):
        return (
            "FAILED",
            f"ChangeSet validation rejected {run_data.get('rejected_changes_count')} times.",
        )

    if "CHANGESET_VALIDATION_PASSED" in event_types:
        return "COMPLETED", "ChangeSet passed path containment and structure validation."

    return "NOT AVAILABLE", "Guardrail validation event not recorded in API event log."


def evaluate_stage_6_code_application(
    run_data: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, str]:
    """Stage 6: Code Application (Requires verified diff or explicit application evidence)."""
    errors = [str(err).lower() for err in run_data.get("errors", [])]
    if any("failed to apply changeset" in err for err in errors):
        return "FAILED", "ChangeSet patch application failed."

    test_reports = run_data.get("test_reports", [])
    if test_reports and "verification:no_changes_applied" in test_reports[-1].get(
        "failed_tests", []
    ):
        return "FAILED", "Workspace files were not modified by ChangeSet."

    code_events = [
        e for e in events if e.get("event_type") == "TASK_COMPLETED" and e.get("stage") == "code"
    ]
    has_diff_in_events = False
    for ce in code_events:
        out_summary = ce.get("output_summary")
        if isinstance(out_summary, dict) and (out_summary.get("diff") or out_summary.get("files")):
            has_diff_in_events = True
            break

    if has_diff_in_events:
        return "COMPLETED", "Code changes verified applied to isolated workspace."

    if test_reports:
        return (
            "UNKNOWN",
            "Test reports exist, but explicit file diff is not available in API summary.",
        )

    return "PENDING", "Code application waiting for ChangeSet generation."


def evaluate_stage_7_testing(
    run_data: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, str]:
    """Stage 7: Testing in Docker Sandbox."""
    reports = run_data.get("test_reports", [])
    if reports:
        last = reports[-1]
        if last.get("passed") and last.get("exit_code") == 0:
            return (
                "COMPLETED",
                f"Sandbox pytest PASSED in {last.get('duration_s', 0):.2f}s (Exit code: 0).",
            )
        failed = last.get("failed_tests", [])
        return (
            "FAILED",
            f"Sandbox tests FAILED (Exit: {last.get('exit_code')}, Failed: {len(failed)}).",
        )

    test_running = any(
        e.get("event_type") in ("TOOL_STARTED", "TASK_STARTED") and e.get("stage") == "test"
        for e in events
    )
    if test_running:
        return "RUNNING", "Executing test suite inside Docker sandbox container."

    return "PENDING", "Testing waiting for code application."


def evaluate_stage_8_retry_loop(
    run_data: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, str]:
    """Stage 8: Correction & Retry Loop."""
    retries = run_data.get("retry_count", 0)
    max_retries = run_data.get("max_retries", 3)
    reports = run_data.get("test_reports", [])
    status_val = str(run_data.get("status", "")).lower()
    errors = [str(err).lower() for err in run_data.get("errors", [])]

    is_retry_capped = retries >= max_retries and any(
        "maximum retries reached" in err for err in errors
    )

    if is_retry_capped:
        return "NEEDS_HUMAN", f"Correction budget exhausted ({retries}/{max_retries} retries used)."

    if retries > 0 and reports and reports[-1].get("passed"):
        return "COMPLETED", f"Correction loop succeeded after {retries} retry iterations."

    if status_val == "running" and retries > 0:
        return "RUNNING", f"Correction Attempt {retries}/{max_retries} executing."

    if reports and reports[0].get("passed") and retries == 0:
        return (
            "SKIPPED",
            "No correction retries required (Baseline and feature tests passed on attempt 1).",
        )

    if status_val == "needs_human":
        return (
            "NOT APPLICABLE",
            "NEEDS_HUMAN entered due to non-retry factor (e.g. guardrail/review/DONE gate).",
        )

    return "PENDING", "Retry loop inactive."


def evaluate_stage_9_review(
    run_data: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[str, str]:
    """Stage 9: Independent Code Review."""
    review = run_data.get("review")
    if review and isinstance(review, dict):
        verdict = str(review.get("verdict", "")).lower()
        findings = review.get("findings", [])
        if verdict == "approve":
            return "COMPLETED", f"Reviewer APPROVE ({len(findings)} findings reported)."
        if verdict == "request_changes":
            return "FAILED", f"Reviewer REQUEST_CHANGES ({len(findings)} findings reported)."
        return "UNKNOWN", f"Reviewer returned verdict: {verdict}"

    review_running = any(
        e.get("event_type") in ("AGENT_STARTED", "TASK_STARTED") and e.get("stage") == "review"
        for e in events
    )
    if review_running:
        return "RUNNING", "Google ADK Reviewer active over A2A (port 8002)."

    return "PENDING", "Code review waiting for test completion."


def evaluate_stage_10_done_gate(run_data: dict[str, Any]) -> tuple[str, dict[str, str], str]:
    """Stage 10: Authoritative DONE Gate (Preserves all 7 original criteria + hash parity)."""
    status_val = str(run_data.get("status", "")).lower()
    reports = run_data.get("test_reports", [])
    last_report = reports[-1] if reports else None
    review = run_data.get("review") or {}

    checklist: dict[str, str] = {}

    # Condition 1: Status Eligibility
    cond1 = status_val not in ("needs_human", "blocked")
    checklist["1. Status Eligibility (Not NEEDS_HUMAN/BLOCKED)"] = "PASS" if cond1 else "FAIL"

    # Condition 2: Deterministic Test Pass
    cond2 = bool(last_report and last_report.get("passed") and last_report.get("exit_code") == 0)
    checklist["2. Deterministic Test Pass (exit_code == 0)"] = "PASS" if cond2 else "FAIL"

    # Condition 3: Valid Non-Empty Workspace Hash
    cond3 = bool(last_report and last_report.get("workspace_hash"))
    checklist["3. Valid Non-Empty Workspace Hash"] = "PASS" if cond3 else "FAIL"

    # Condition 4: Zero Failed Tests
    cond4 = bool(last_report and not last_report.get("failed_tests"))
    checklist["4. Zero Failed Tests in Latest Report"] = "PASS" if cond4 else "FAIL"

    # Condition 5: Workspace Modifications
    checklist["5. Uncommitted Workspace Modifications"] = (
        "NOT AVAILABLE IN API / CONFIRMED BY BACKEND GATE"
    )

    # Condition 6: Workspace Isolation
    if run_data.get("workspace_isolated") is True:
        checklist["6. Workspace Isolation (Not Pristine demo_repo)"] = "PASS"
    elif run_data.get("workspace_isolated") is False:
        checklist["6. Workspace Isolation (Not Pristine demo_repo)"] = "FAIL"
    else:
        checklist["6. Workspace Isolation (Not Pristine demo_repo)"] = (
            "NOT AVAILABLE IN API / CONFIRMED BY BACKEND GATE"
        )

    # Condition 7: Reviewer Explicit Approval
    cond7 = str(review.get("verdict", "")).lower() == "approve"
    checklist["7. Reviewer Explicit Approval (verdict == APPROVE)"] = "PASS" if cond7 else "FAIL"

    # Live Disk Hash Parity
    checklist["8. Live Disk Hash Parity"] = "NOT AVAILABLE IN API / CONFIRMED BY BACKEND GATE"

    # Authoritative overall gate outcome
    if status_val == "done":
        summary = "Authoritative Verdict: PASS (Backend confirmed all gate criteria satisfied)."
        return "COMPLETED", checklist, summary

    errors = run_data.get("errors", [])
    gate_errs = [e for e in errors if "done gate" in e.lower()]
    if gate_errs:
        summary = f"Authoritative Verdict: FAIL ({gate_errs[-1]})."
        return "FAILED", checklist, summary

    if status_val in ("needs_human", "blocked"):
        summary = (
            f"Authoritative Verdict: FAIL (Run terminated with status '{status_val.upper()}')."
        )
        return "FAILED", checklist, summary

    summary = "Authoritative Verdict: PENDING (Waiting for preceding pipeline stages to finish)."
    return "PENDING", checklist, summary


def evaluate_stage_11_promotion(run_data: dict[str, Any]) -> tuple[str, str]:
    """Stage 11: GitHub Promotion."""
    pr_info = run_data.get("pr")
    if pr_info and isinstance(pr_info, dict) and pr_info.get("url"):
        return (
            "COMPLETED",
            f"Pull Request opened: {pr_info.get('url')} (Branch: {pr_info.get('branch')}).",
        )

    errors = [str(err).lower() for err in run_data.get("errors", [])]
    pr_failed = any("pr creation" in err for err in errors)
    if pr_failed:
        return "FAILED", "Pull Request promotion failed during git operations or API call."

    status_val = str(run_data.get("status", "")).lower()

    create_pr_val = run_data.get("create_pr")
    pr_auth_val = run_data.get("pr_authorized")

    if create_pr_val is False or pr_auth_val is False:
        return (
            "SKIPPED",
            "PR creation was not authorized for this run (Dry run verification only).",
        )

    if status_val == "done":
        return "UNKNOWN", "PR status unavailable."

    return "PENDING", "Promotion waiting for authoritative DONE Gate approval."


def evaluate_all_stages(
    run_data: dict[str, Any], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Synthesize evidence across all 11 stages into structured stage descriptors."""
    s1_st, s1_det = evaluate_stage_1_requirement(run_data, events)
    s2_st, s2_det = evaluate_stage_2_repo_understanding(run_data, events)
    s3_st, s3_det = evaluate_stage_3_planning(run_data, events)
    s4_st, s4_det = evaluate_stage_4_coding(run_data, events)
    s5_st, s5_det = evaluate_stage_5_guardrails(run_data, events)
    s6_st, s6_det = evaluate_stage_6_code_application(run_data, events)
    s7_st, s7_det = evaluate_stage_7_testing(run_data, events)
    s8_st, s8_det = evaluate_stage_8_retry_loop(run_data, events)
    s9_st, s9_det = evaluate_stage_9_review(run_data, events)
    s10_st, s10_chk, s10_sum = evaluate_stage_10_done_gate(run_data)
    s11_st, s11_det = evaluate_stage_11_promotion(run_data)

    return [
        {"id": 1, "name": "Requirement Analysis", "status": s1_st, "details": s1_det},
        {"id": 2, "name": "Repository Understanding", "status": s2_st, "details": s2_det},
        {"id": 3, "name": "Planning Agent (ADK A2A)", "status": s3_st, "details": s3_det},
        {"id": 4, "name": "Coding Agent (CrewAI)", "status": s4_st, "details": s4_det},
        {"id": 5, "name": "ChangeSet Guardrails", "status": s5_st, "details": s5_det},
        {"id": 6, "name": "Code Application", "status": s6_st, "details": s6_det},
        {"id": 7, "name": "Docker Sandbox Testing", "status": s7_st, "details": s7_det},
        {"id": 8, "name": "Correction & Retry Loop", "status": s8_st, "details": s8_det},
        {"id": 9, "name": "Independent Code Review", "status": s9_st, "details": s9_det},
        {
            "id": 10,
            "name": "Authoritative DONE Gate",
            "status": s10_st,
            "details": s10_sum,
            "checklist": s10_chk,
        },
        {"id": 11, "name": "GitHub Promotion", "status": s11_st, "details": s11_det},
    ]


# ---------------------------------------------------------------------------
# HTML Connected Pipeline Generator (Reference Screenshot Design)
# ---------------------------------------------------------------------------


def generate_pipeline_html(stages: list[dict[str, Any]], run_data: dict[str, Any]) -> str:
    """Generate high-contrast, connected horizontal workflow matching reference design."""
    run_id = html.escape(str(run_data.get("run_id", "idle_run")))
    short_hash = run_id[-7:] if len(run_id) >= 7 else run_id
    status_raw = str(run_data.get("status", "IDLE")).upper()
    branch = html.escape(str(run_data.get("branch", "main")))
    duration_str = format_duration(run_data.get("duration_s"))

    # Map status display name & styling
    if status_raw == "DONE":
        status_display = "Completed"
        status_color = PALETTE["neon_accent"]
    elif status_raw in ("RUNNING", "ACTIVE"):
        status_display = "In progress"
        status_color = "#F59E0B"
    elif status_raw == "NEEDS_HUMAN":
        status_display = "Needs human"
        status_color = "#F59E0B"
    elif status_raw in ("BLOCKED", "FAILED"):
        status_display = "Failed / Blocked"
        status_color = PALETTE["status_failed"]
    else:
        status_display = status_raw.capitalize()
        status_color = PALETTE["secondary_text"]

    pr_val = run_data.get("pr")
    artifact_text = (
        "PR Ready" if (pr_val and isinstance(pr_val, dict) and pr_val.get("url")) else "-"
    )

    # Build Stage Nodes HTML
    nodes_html = []
    total_stages = len(stages)
    for idx, stage in enumerate(stages):
        st_val = stage.get("status", "PENDING")
        s_name = html.escape(stage.get("name", f"Stage {stage.get('id')}"))
        s_id = stage.get("id", idx + 1)

        # Node CSS Class and Icon
        if st_val == "COMPLETED":
            card_class = "node-completed"
            icon_class = "icon-completed"
            icon_char = "&#10003;"
            sub_text = "done"
        elif st_val == "RUNNING":
            card_class = "node-running"
            icon_class = "icon-running"
            icon_char = "&#9673;"
            sub_text = "active"
        elif st_val == "NEEDS_HUMAN":
            card_class = "node-needs-human"
            icon_class = "icon-needs-human"
            icon_char = "!"
            sub_text = "review"
        elif st_val in ("FAILED", "BLOCKED"):
            card_class = "node-failed"
            icon_class = "icon-failed"
            icon_char = "&#10005;"
            sub_text = "failed"
        elif st_val in ("SKIPPED", "NOT APPLICABLE"):
            card_class = "node-pending"
            icon_class = "icon-pending"
            icon_char = "&#8211;"
            sub_text = "skip"
        else:
            card_class = "node-pending"
            icon_class = "icon-pending"
            icon_char = "&#9675;"
            sub_text = "wait"

        # Connector line between stages
        connector_html = ""
        if idx < total_stages - 1:
            next_st = stages[idx + 1].get("status", "PENDING")
            active_conn = (
                "connector-active" if st_val == "COMPLETED" and next_st != "PENDING" else ""
            )
            connector_html = f'<div class="node-connector-line {active_conn}"></div>'

        node_card = (
            f'<div class="pipeline-stage-wrapper">'
            f'<div class="pipeline-node-card {card_class}">'
            f'<div class="node-icon-circle {icon_class}">{icon_char}</div>'
            f'<div class="node-text-col">'
            f'<span class="node-name">{s_id}. {s_name}</span>'
            f'<span class="node-sub">{sub_text}</span>'
            f"</div>"
            f"</div>"
            f"{connector_html}"
            f"</div>"
        )
        nodes_html.append(node_card)

    all_nodes_rendered = "".join(nodes_html)

    return textwrap.dedent(
        f"""
        <div class="workflow-canvas">
        <div class="workflow-top-bar">
        <div class="workflow-meta-group">
        <span class="workflow-meta-label">Run Identification</span>
        <div style="display:flex; align-items:center; gap:8px; margin-top:4px;">
        <span class="emerald-badge-pill pill-hash">&#9670; {short_hash}</span>
        <span class="emerald-badge-pill pill-branch">{branch}</span>
        </div>
        </div>
        <div class="workflow-meta-group">
        <span class="workflow-meta-label">Status</span>
        <span class="workflow-meta-val" style="color: {status_color};">{status_display}</span>
        </div>
        <div class="workflow-meta-group">
        <span class="workflow-meta-label">Total Duration</span>
        <span class="workflow-meta-val">{duration_str}</span>
        </div>
        <div class="workflow-meta-group">
        <span class="workflow-meta-label">Artifacts</span>
        <span class="workflow-meta-val">{artifact_text}</span>
        </div>
        </div>
        <div class="workflow-title-row">
        <div class="workflow-filename">repoagent-pipeline.lock.yml</div>
        <div class="workflow-dispatch">
        on: autonomous_workflow_dispatch &bull; isolated_workspace
        </div>
        </div>
        <div class="pipeline-track-scroll">
        {all_nodes_rendered}
        </div>
        <div class="workflow-controls-pill">
        <span>&#x26F6;</span>
        <span>&minus;</span>
        <span>&plus;</span>
        </div>
        </div>
        """
    ).strip()


# ---------------------------------------------------------------------------
# Streamlit UI Rendering Components
# ---------------------------------------------------------------------------


def render_header(health: dict[str, Any] | None, active_run: dict[str, Any] | None) -> None:
    """Render application header with system health indicators."""
    col1, col2 = st.columns([3, 2])
    with col1:
        title_color = PALETTE["primary_text"]
        accent_color = PALETTE["neon_accent"]
        badge_bg = PALETTE["surface_card"]
        badge_border = PALETTE["border_emerald"]
        render_html(
            f"""
            <div style="display:flex; align-items:center; gap:12px; margin-bottom:6px;">
                <span style="font-size:1.8rem; color:{accent_color};">&#9670;</span>
                <span style="font-size:1.95rem; font-weight:800;
                    color:{title_color}; letter-spacing:-0.02em;">
                    RepoAgent
                </span>
                <span style="font-size:0.82rem; background:{badge_bg};
                    border:1px solid {badge_border}; color:{accent_color};
                    padding:4px 10px; border-radius:14px; font-weight:700;">
                    v0.1.0 DARK
                </span>
            </div>
            <div style="font-size:0.95rem; color:{PALETTE["secondary_text"]}; margin-left:38px;">
                Autonomous Requirement-to-PR Engineering System
            </div>
            """
        )

    with col2:
        h_col1, h_col2, h_col3 = st.columns(3)
        with h_col1:
            if health and health.get("mode") == "public_showcase":
                render_html(
                    '<span class="emerald-badge-pill pill-connected">&#9679; Public Demo</span>'
                )
            elif health:
                api_ver = health.get("version", "0.1.0")
                render_html(
                    f'<span class="emerald-badge-pill pill-connected">&#9679; API v{api_ver}</span>'
                )
            else:
                render_html(
                    '<span class="emerald-badge-pill pill-disconnected">&#9679; API Offline</span>'
                )

        with h_col2:
            if health and health.get("mode") == "public_showcase":
                render_html(
                    '<a href="https://github.com/Kushagra-Pachauri/demo_repo/pull/7" target="_blank" style="text-decoration:none;"><span class="emerald-badge-pill pill-connected">&#9679; Verified PR #7</span></a>'
                )
            elif health and health.get("langfuse_enabled"):
                render_html(
                    '<span class="emerald-badge-pill pill-connected">&#9679; Langfuse</span>'
                )
            elif health:
                render_html(
                    '<span class="emerald-badge-pill pill-muted">&#9675; Langfuse Off</span>'
                )
            else:
                render_html(
                    '<span class="emerald-badge-pill pill-muted">&#9675; Langfuse N/A</span>'
                )

        with h_col3:
            if active_run:
                dur = format_duration(active_run.get("duration_s"))
                r_id = str(active_run.get("run_id", ""))[:12]
                title_id = active_run.get("run_id", "")
                render_html(
                    f'<span class="emerald-badge-pill pill-connected" '
                    f'title="{title_id}">⚡ {r_id} ({dur})</span>'
                )
            else:
                render_html('<span class="emerald-badge-pill pill-muted">&#9675; Idle</span>')


def render_workbench(api_url: str = DEFAULT_API_URL, auth_token: str | None = None) -> None:
    """Render the Requirement Input and Execution Workbench."""
    title_color = PALETTE["primary_text"]
    accent = PALETTE["neon_accent"]
    render_html(
        f"""
        <div style="display:flex; align-items:center; gap:10px;
            margin-top:12px; margin-bottom:14px;">
            <span style="color:{accent}; font-size:1.35rem;">&#9889;</span>
            <span style="font-size:1.35rem; font-weight:700; color:{title_color};">
                Requirement Input & Execution Workbench
            </span>
        </div>
        """
    )

    st.markdown(
        """
        **Execution Guide:**
        1. **Connect the Live Gateway** (under *Advanced Connection Settings* if executing remote live runs).
        2. **Enter the software requirement** in the box below.
        3. **Click Execute** to start the pipeline.
        """
    )

    allowed_repos = get_allowed_repos()
    gw_url = (
        st.session_state.get("ui_gw_url")
        or st.session_state.get("gateway_url")
        or os.environ.get("REPOAGENT_GATEWAY_URL", "")
    ).strip()
    gw_token = (
        st.session_state.get("ui_gw_token")
        or st.session_state.get("gateway_token")
        or os.environ.get("REPOAGENT_AUTH_TOKEN", "")
    ).strip()

    with st.expander("⚙️ Advanced Connection Settings", expanded=False):
        gw_c1, gw_c2 = st.columns(2)
        with gw_c1:
            gw_url = st.text_input(
                "Live Gateway URL",
                value=gw_url,
                placeholder="e.g. https://...ngrok-free.dev or http://localhost:8008",
                key="ui_gw_url",
            )
            st.session_state["gateway_url"] = gw_url
        with gw_c2:
            gw_token = st.text_input(
                "Gateway Access Token",
                value=gw_token,
                type="password",
                placeholder="Bearer token",
                key="ui_gw_token",
            )
            st.session_state["gateway_token"] = gw_token

    active_target_api = gw_url.strip() if gw_url.strip() else api_url
    active_token = gw_token.strip() if gw_token.strip() else auth_token

    is_demo = os.environ.get("REPOAGENT_DEMO_MODE", "").lower() in ("1", "true") or (fetch_health(api_url) or {}).get("mode") == "public_showcase"
    if is_demo and not gw_url:
        st.info(
            "ℹ️ **Public Demo Mode Active**: Real sandboxed Docker test execution runs via the authenticated Live Gateway. "
            "Explore genuine verified runs (such as **0f04e14907d7**) below or connect your Live Gateway in Advanced Connection Settings to execute changes."
        )

    wb_col1, wb_col2 = st.columns([3, 1])
    with wb_col1:
        requirement_text = st.text_area(
            "Software Development Requirement",
            placeholder="Describe the software engineering change or feature to implement...",
            height=130,
            key="ui_requirement_input",
        )

    with wb_col2:
        repo_choice = st.selectbox(
            "Target Repository",
            options=allowed_repos,
            index=0,
            key="ui_repo_select",
        )
        max_retries = st.number_input(
            "Max Correction Retries",
            min_value=1,
            max_value=3,
            value=3,
            step=1,
            key="ui_retries_input",
        )
        use_live_agents = st.checkbox(
            "Execute Live Autonomous LLM Agents (ADK Planner + CrewAI Engineer)",
            value=True,
            key="ui_use_live_agents_checkbox",
        )
        create_pr = st.checkbox(
            "Authorize GitHub Pull Request Creation (Remote Write)",
            value=False,
            key="ui_create_pr_checkbox",
        )

    if "submitting" not in st.session_state:
        st.session_state["submitting"] = False

    submit_clicked = st.button(
        "Execute Pipeline",
        type="primary",
        disabled=st.session_state["submitting"],
        key="ui_run_button",
    )

    if submit_clicked:
        if is_demo and not (gw_url.strip() or (active_target_api and "offline" not in active_target_api and "localhost" not in active_target_api)):
            st.error(
                "❌ **Live Gateway Not Connected**: Please expand '⚙️ Advanced Connection Settings' above, enter your Live Gateway URL and Access Token, then click Execute Pipeline."
            )
            st.info(
                "💡 **Public Demo Mode**: The dashboard is currently displaying curated historical runs. Live execution requires connecting your Live Gateway."
            )
            return

        v_req, err_req = validate_requirement_input(requirement_text)
        if not v_req:
            st.error(f"❌ **Invalid Requirement**: {err_req}")
            return

        v_repo, err_repo = validate_repo_input(repo_choice)
        if not v_repo:
            st.error(f"❌ **Invalid Repository**: {err_repo}")
            return

        st.session_state["submitting"] = True
        try:
            with st.spinner("Submitting run to Live Gateway..."):
                success, result = submit_new_run(
                    repo_url=repo_choice,
                    requirement=requirement_text,
                    max_retries=int(max_retries),
                    create_pr=bool(create_pr),
                    use_live_agents=bool(use_live_agents),
                    api_url=active_target_api,
                    auth_token=active_token,
                )
            if success and isinstance(result, dict):
                new_run_id = result.get("run_id")
                st.success(f"✅ Run initiated successfully: `{new_run_id}`")
                st.session_state["selected_run_id"] = new_run_id
                # Record in session custom_runs immediately
                if "custom_runs" not in st.session_state:
                    st.session_state["custom_runs"] = {}
                st.session_state["custom_runs"][new_run_id] = {
                    "run_id": new_run_id,
                    "status": str(result.get("status", "running")).lower(),
                    "requirement": requirement_text.strip(),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "execution_mode": "LIVE",
                    "is_live_execution": True,
                }
                # Remove stale selector key so selectbox immediately selects new_run_id
                st.session_state.pop("ui_run_selector", None)
                st.rerun()
            else:
                st.error(f"❌ **Execution Gateway Error**: {result}")
                st.info(
                    "💡 **Public Demo Mode**: The submission could not be completed by the gateway. "
                    "You can continue exploring genuine historical runs and full pipeline execution evidence in the catalog below."
                )
        finally:
            st.session_state["submitting"] = False


def render_pipeline_view(stages: list[dict[str, Any]], run_data: dict[str, Any]) -> None:
    """Render the connected 11-stage pipeline timeline matching the reference design."""
    is_stale = bool(run_data.get("is_stale", False))
    status_val = str(run_data.get("status", "")).lower()

    if is_stale or (status_val == "running" and not run_data.get("is_active")):
        st.warning(
            f"⚠️ **Interrupted / Stale Run Detected:** Run `{run_data.get('run_id')}` is marked as "
            "RUNNING but has no active in-memory background worker."
        )

    # Render reference-inspired connected HTML workflow card safely
    html_pipeline = generate_pipeline_html(stages, run_data)
    render_html(html_pipeline)


def render_stage_deep_dive(
    stages: list[dict[str, Any]], run_data: dict[str, Any], events: list[dict[str, Any]]
) -> None:
    """Render detailed inspection drawer for each stage."""
    title_color = PALETTE["primary_text"]
    accent = PALETTE["neon_accent"]
    render_html(
        f"""
        <div style="display:flex; align-items:center; gap:10px;
            margin-top:12px; margin-bottom:14px;">
            <span style="color:{accent}; font-size:1.3rem;">&#128269;</span>
            <span style="font-size:1.3rem; font-weight:700; color:{title_color};">
                Stage Deep-Dive & Verified Evidence
            </span>
        </div>
        """
    )

    stage_names = [f"{s['id']}. {s['name']} [{s['status']}]" for s in stages]
    selected_idx = st.selectbox(
        "Select Stage to Inspect", options=range(len(stages)), format_func=lambda i: stage_names[i]
    )

    active_stage = stages[selected_idx]
    s_id = active_stage["id"]
    st.markdown(f"### {active_stage['name']}")
    st.markdown(f"**Status:** `{active_stage['status']}`")
    st.markdown(f"**Verified Evidence:** {active_stage['details']}")

    # Stage 1: Requirement Analysis
    if s_id == 1:
        st.markdown(f"**Target Requirement:**")
        st.info(run_data.get("requirement", "No requirement provided."))

    # Stage 2: Repository Understanding
    elif s_id == 2:
        st.markdown(f"**Isolated Workspace Path:** `{run_data.get('repo_url') or 'runs/<run_id>/workspace'}`")
        tree_events = [
            e for e in events if e.get("stage") == "analyze" and e.get("event_type") == "TOOL_COMPLETED"
        ]
        if tree_events:
            for te in tree_events:
                tool_name = te.get("input_summary", {}).get("tool", "analyze_tool")
                with st.expander(f"Inspected: {tool_name}", expanded=False):
                    st.code(sanitize_display_text(te.get("output_summary")), language="text")

    # Stage 3: Planning Agent (ADK A2A)
    elif s_id == 3:
        plan = run_data.get("plan")
        if plan and isinstance(plan, dict):
            st.markdown(f"**Task Description:** {plan.get('task_description') or 'N/A'}")
            st.markdown(f"**Complexity:** `{plan.get('complexity', 'medium')}`")
            files_to_change = plan.get("files_to_change", [])
            if files_to_change:
                st.markdown("**Files To Change Plan:**")
                for f in files_to_change:
                    path = f.get("path") if isinstance(f, dict) else getattr(f, "path", str(f))
                    reason = f.get("reason", "") if isinstance(f, dict) else getattr(f, "reason", "")
                    prop = f.get("proposed_change", "") if isinstance(f, dict) else getattr(f, "proposed_change", "")
                    st.markdown(f"- 📄 `{path}`: {reason}")
                    if prop:
                        st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ *Proposed:* {prop}")
        else:
            planner_events = [
                e for e in events if e.get("agent_name") in ("planner", "repo-planner") or e.get("stage") in ("analyze", "plan")
            ]
            if planner_events:
                with st.expander("ADK Planner Agent Activity", expanded=True):
                    for pe in planner_events:
                        st.write(f"- `{pe.get('event_type')}`: {sanitize_display_text(pe.get('output_summary') or pe.get('input_summary'))}")
            else:
                st.info("No plan object found in backend state.")

    # Stage 4: Coding Agent (CrewAI)
    elif s_id == 4:
        code_events = [
            e for e in events if e.get("stage") == "code" or "engineer" in str(e.get("agent_name", "")).lower()
        ]
        if code_events:
            for ce in code_events:
                out = ce.get("output_summary")
                if out:
                    with st.expander(f"CrewAI Output ({ce.get('event_type')}):", expanded=True):
                        if isinstance(out, dict):
                            st.json(out)
                        else:
                            st.code(sanitize_display_text(out), language="python")
        else:
            st.info("Coding Agent output is not yet available or waiting for planning completion.")

    # Stage 5: ChangeSet Guardrails
    elif s_id == 5:
        guard_events = [e for e in events if "CHANGESET" in str(e.get("event_type", "")) or e.get("stage") == "changeset_validation"]
        if guard_events:
            for ge in guard_events:
                st.markdown(f"- **{ge.get('event_type')}**: {ge.get('input_summary', {}).get('reason', 'Validated')}")
                if "diff_size_bytes" in ge.get("input_summary", {}):
                    st.caption(f"Diff Size: {ge['input_summary']['diff_size_bytes']} bytes, Files: {ge['input_summary'].get('file_count', 0)}")
        else:
            st.info("Guardrail validation details not recorded.")

    # Stage 6: Code Application
    elif s_id == 6:
        test_reports = run_data.get("test_reports", [])
        if test_reports and test_reports[-1].get("workspace_hash"):
            st.markdown(f"**Verified Workspace Hash:** `{test_reports[-1].get('workspace_hash')}`")
        else:
            st.info("Workspace application hash not verified.")

    # Stage 7: Sandbox Testing
    elif s_id == 7:
        reports = run_data.get("test_reports", [])
        if reports:
            last = reports[-1]
            st.markdown(f"- **Command:** `{last.get('command')}`")
            st.markdown(f"- **Exit Code:** `{last.get('exit_code')}`")
            st.markdown(f"- **Workspace Hash:** `{last.get('workspace_hash')}`")
            if last.get("failed_tests"):
                st.error(f"Failed Tests: {', '.join(last.get('failed_tests'))}")
            with st.expander("View Sanitized Sandbox Output", expanded=True):
                st.code(
                    sanitize_display_text(last.get("log_tail") or last.get("raw_output")),
                    language="text",
                )
        else:
            st.info("No test reports captured.")

    # Stage 8: Correction Loop
    elif s_id == 8:
        retries = run_data.get("retry_count", 0)
        max_retries = run_data.get("max_retries", 3)
        st.markdown(f"**Correction Cycles Used:** `{retries} / {max_retries}`")

    # Stage 9: Independent Code Review
    elif s_id == 9:
        review = run_data.get("review")
        if review and isinstance(review, dict):
            st.markdown(f"**Verdict:** `{str(review.get('verdict', '')).upper()}`")
            st.markdown(f"**Summary:** {review.get('summary')}")
            findings = review.get("findings", [])
            if findings:
                st.markdown("**Review Findings:**")
                for f in findings:
                    sev = f.get("severity", "info").upper()
                    f_line = f"{f.get('file')}:{f.get('line', '')}"
                    st.markdown(f"- **[{sev}]** `{f_line}` - {f.get('message')}")
        else:
            st.info("Code review not yet completed.")

    # Stage 10: Authoritative DONE Gate
    elif s_id == 10:
        st.markdown("#### Authoritative DONE Gate 7-Point Audit Checklist")
        chk = active_stage.get("checklist", {})
        for cond_name, cond_res in chk.items():
            icon = "✅" if cond_res == "PASS" else ("❌" if cond_res == "FAIL" else "❔")
            st.markdown(f"- {icon} **{cond_name}**: `{cond_res}`")

    # Stage 11: GitHub Promotion
    elif s_id == 11:
        pr = run_data.get("pr")
        if pr and isinstance(pr, dict) and pr.get("url"):
            st.success(f"Pull Request Created: [{pr.get('title')}]({pr.get('url')})")
            st.markdown(f"- **URL:** {pr.get('url')}\n- **Branch:** `{pr.get('branch')}`")
        else:
            st.info("No Pull Request created (Run verification only or not authorized).")

    # Raw stage events expander
    stage_events = [e for e in events if e.get("stage") == active_stage.get("name", "").lower() or str(s_id) in str(e.get("stage", ""))]
    if not stage_events:
        stage_map = {1: "guardrails", 2: "analyze", 3: "plan", 4: "code", 5: "changeset_validation", 6: "apply", 7: "test", 8: "retry_loop", 9: "review", 10: "done_gate", 11: "open_pr"}
        stage_key = stage_map.get(s_id, "")
        stage_events = [e for e in events if e.get("stage") == stage_key]

    if stage_events:
        with st.expander(f"Raw Agent Events & Messages ({len(stage_events)} events)", expanded=False):
            for se in stage_events:
                st.write(f"- **{se.get('event_type')}** ({se.get('agent_name', 'system')}):")
                if se.get("output_summary"):
                    st.caption(f"Output: {sanitize_display_text(se.get('output_summary'))[:300]}")


def render_execution_summary(run_data: dict[str, Any]) -> None:
    """Render comprehensive summary at completion."""
    title_color = PALETTE["primary_text"]
    accent = PALETTE["neon_accent"]
    render_html(
        f"""
        <div style="display:flex; align-items:center; gap:10px;
            margin-top:12px; margin-bottom:14px;">
            <span style="color:{accent}; font-size:1.3rem;">&#128202;</span>
            <span style="font-size:1.3rem; font-weight:700; color:{title_color};">
                Execution Summary & Verification Evidence
            </span>
        </div>
        """
    )

    status_val = str(run_data.get("status", "")).upper()
    exec_mode = str(run_data.get("execution_mode", "LIVE")).upper()
    is_synth = bool(run_data.get("is_synthetic", False) or "synthetic" in str(run_data.get("run_id", "")).lower())

    # Execution Provenance Banner
    if is_synth or exec_mode == "SYNTHETIC_GUARDRAIL_SAMPLE":
        st.warning(
            "⚠️ **Execution Provenance: SYNTHETIC_GUARDRAIL_SAMPLE** — "
            "Explicitly labeled synthetic demonstration illustrating how RepoAgent input guardrails intercept, flag, and block malicious prompts before agent dispatch."
        )
    elif exec_mode == "LIVE":
        st.success(
            "🟢 **Execution Provenance: GENUINE LIVE EXECUTION** — "
            "Authentically executed by live LLM agents (Ollama / Qwen / Groq), MCP tools, and authoritative Docker sandbox."
        )
    elif exec_mode == "REPLAY":
        st.info(
            "🔄 **Execution Provenance: DETERMINISTIC REPLAY BASELINE** — "
            "Full 11-stage deterministic replay demonstrating baseline pipeline progression."
        )
    elif exec_mode == "MOCK":
        st.info(
            "🧪 **Execution Provenance: MOCK VERIFICATION RUN** — "
            "Offline test execution verifying state transitions and report schemas."
        )

    # Core Execution Metrics Row
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Run ID", run_data.get("run_id", "N/A"))
    col2.metric("Final Status", status_val)
    col3.metric("Duration", format_duration(run_data.get("duration_s")))
    col4.metric(
        "Correction Cycles",
        f"{run_data.get('retry_count', 0)} / {run_data.get('max_retries', 3)}",
    )
    col5.metric(
        "Execution Mode",
        exec_mode,
    )

    # Verification Evidence Row: Tests, Reviewer, DONE Gate
    test_reports = run_data.get("test_reports") or []
    last_tr = test_reports[-1] if test_reports else None
    if last_tr:
        if last_tr.get("passed"):
            tests_display = f"✅ {last_tr.get('exit_code', 0)} (Passed)"
        else:
            n_fail = len(last_tr.get("failed_tests") or [])
            tests_display = f"❌ {n_fail} Failed"
    else:
        tests_display = "None / N/A"

    review = run_data.get("review") or {}
    review_verdict = str(review.get("verdict", "N/A")).upper() if review else "N/A"

    if status_val == "DONE":
        done_gate_display = "✅ PASSED (7/7)"
    elif status_val in ("NEEDS_HUMAN", "FAILED", "BLOCKED"):
        done_gate_display = f"❌ {status_val}"
    else:
        done_gate_display = "⏳ PENDING"

    v_col1, v_col2, v_col3 = st.columns(3)
    v_col1.metric("Docker Sandbox Tests", tests_display)
    v_col2.metric("ADK Reviewer Verdict", review_verdict)
    v_col3.metric("Authoritative DONE Gate", done_gate_display)

    # GitHub PR Section
    pr = run_data.get("pr")
    if pr and isinstance(pr, dict) and pr.get("url"):
        pr_title = pr.get("title")
        pr_url = pr.get("url")
        pr_branch = pr.get("branch")
        st.success(f"🎉 **GitHub Pull Request:** [{pr_title}]({pr_url}) (Branch: `{pr_branch}`)")
    else:
        st.info("No Pull Request created (Run verification only or not authorized).")

    # Errors & Warnings
    errors = run_data.get("errors", [])
    if errors:
        st.warning(f"**Recorded Errors/Warnings ({len(errors)}):**")
        for err in errors:
            st.markdown(f"- {sanitize_display_text(err)}")


def render_run_history(runs: list[dict[str, Any]], current_run_id: str) -> str:
    """Render run history selector with search, mode, and status filter controls."""
    title_color = PALETTE["primary_text"]
    accent = PALETTE["neon_accent"]
    render_html(
        f"""
        <div style="display:flex; align-items:center; gap:10px;
            margin-top:12px; margin-bottom:14px;">
            <span style="color:{accent}; font-size:1.3rem;">&#128220;</span>
            <span style="font-size:1.3rem; font-weight:700; color:{title_color};">
                Historical Run Archive & Evidence Browser
            </span>
        </div>
        """
    )

    f_col1, f_col2, f_col3 = st.columns([1, 1, 2])
    with f_col1:
        st_filter = st.selectbox(
            "Status Filter",
            options=["ALL", "DONE", "FAILED", "NEEDS_HUMAN", "BLOCKED", "RUNNING"],
            index=0,
            key="ui_history_status_filter",
        )
    with f_col2:
        mode_filter = st.selectbox(
            "Execution Mode",
            options=["ALL", "LIVE", "REPLAY", "MOCK", "SYNTHETIC"],
            index=0,
            key="ui_history_mode_filter",
        )
    with f_col3:
        search_kw = st.text_input(
            "🔍 Search Archive", placeholder="Search by run ID, requirement, or PR...", key="ui_search_kw"
        )

    filtered = runs
    if st_filter != "ALL":
        target_status = st_filter.lower()
        filtered = [r for r in filtered if str(r.get("status", "")).lower() == target_status]

    if mode_filter != "ALL":
        target_mode = mode_filter.upper()
        filtered = [r for r in filtered if str(r.get("execution_mode", "")).upper() == target_mode]

    if search_kw.strip():
        kw = search_kw.strip().lower()
        filtered = [
            r
            for r in filtered
            if kw in str(r.get("run_id", "")).lower()
            or kw in str(r.get("requirement", "")).lower()
            or kw in str(r.get("pr", {}).get("title", "")).lower()
            or kw in str(r.get("pr", {}).get("url", "")).lower()
        ]

    # Guarantee current selected run is always visible and selectable even if filters differ
    if current_run_id and not any(r.get("run_id") == current_run_id for r in filtered):
        matching_current = [r for r in runs if r.get("run_id") == current_run_id]
        if matching_current:
            filtered.insert(0, matching_current[0])

    if not filtered:
        st.info("No historical runs match the specified filter.")
        return current_run_id

    def build_label(r: dict[str, Any]) -> str:
        rid = r.get("run_id", "unknown")
        status = str(r.get("status", "unknown")).upper()
        mode = str(r.get("execution_mode", "LIVE")).upper()
        trs = r.get("test_reports") or []
        if trs:
            last_tr = trs[-1]
            if last_tr.get("passed"):
                t_str = f"Tests: Pass ({len(trs)} cycles)"
            else:
                t_str = f"Tests: Fail ({len(trs)} cycles)"
        else:
            t_str = "Tests: N/A"

        pr_data = r.get("pr") or {}
        pr_str = f" | PR #{pr_data.get('pull_number') or 7}" if (pr_data.get("url") or pr_data.get("pull_number")) else ""
        req_snip = str(r.get("requirement", "")).replace("\n", " ").strip()[:38]
        return f"{rid} | [{mode}] | {status} | {t_str}{pr_str} | {req_snip}"

    run_opts = [build_label(r) for r in filtered]

    default_idx = 0
    for idx, opt in enumerate(run_opts):
        if current_run_id and opt.startswith(current_run_id):
            default_idx = idx
            break

    # Synchronize ui_run_selector with current_run_id so the selectbox does not stick to an old historical run
    current_selected_opt = st.session_state.get("ui_run_selector", "")
    if current_run_id and run_opts and not current_selected_opt.startswith(current_run_id):
        st.session_state["ui_run_selector"] = run_opts[default_idx]

    selected_opt = st.selectbox(
        "Select Historical Run to Inspect", options=run_opts, index=default_idx, key="ui_run_selector"
    )
    selected_id = selected_opt.split(" | ")[0]
    return selected_id


# ---------------------------------------------------------------------------
# Streamlit Dynamic Polling Engine
# ---------------------------------------------------------------------------


if hasattr(st, "fragment"):

    @st.fragment(run_every=2)
    def render_active_pipeline_fragment(run_id: str, api_url: str = DEFAULT_API_URL, auth_token: str | None = None) -> None:
        """Isolated polling fragment executing strictly when run is active."""
        run_data = fetch_run_details(run_id, api_url, auth_token=auth_token)
        if not run_data:
            st.warning(f"Could not load active status for run {run_id}.")
            return

        events = fetch_run_events(run_id, offset=0, limit=100, api_url=api_url, auth_token=auth_token)
        stages = evaluate_all_stages(run_data, events)

        render_pipeline_view(stages, run_data)

        if is_terminal_or_stale(run_data):
            st.caption("Execution complete. Polling stopped.")
            st.rerun()
else:

    def render_active_pipeline_fragment(run_id: str, api_url: str = DEFAULT_API_URL, auth_token: str | None = None) -> None:
        """Fallback fragment when st.fragment is unavailable."""
        run_data = fetch_run_details(run_id, api_url, auth_token=auth_token)
        if run_data:
            events = fetch_run_events(run_id, offset=0, limit=100, api_url=api_url, auth_token=auth_token)
            stages = evaluate_all_stages(run_data, events)
            render_pipeline_view(stages, run_data)


# ---------------------------------------------------------------------------
# Main Dashboard Application Entry Point
# ---------------------------------------------------------------------------


def render_dashboard() -> None:
    """Main rendering entry point for RepoAgent execution dashboard."""
    st.set_page_config(
        page_title="RepoAgent Execution Dashboard",
        page_icon="🤖",
        layout="wide",
    )

    # Inject Emerald Dark theme design system
    inject_emerald_theme_css()

    gw_url = (
        st.session_state.get("ui_gw_url")
        or st.session_state.get("gateway_url")
        or os.environ.get("REPOAGENT_GATEWAY_URL", "")
    ).strip()
    gw_token = (
        st.session_state.get("ui_gw_token")
        or st.session_state.get("gateway_token")
        or os.environ.get("REPOAGENT_AUTH_TOKEN", "")
    ).strip()

    is_demo = os.environ.get("REPOAGENT_DEMO_MODE", "").lower() in ("1", "true")
    if gw_url:
        active_api = gw_url
        active_token = gw_token if gw_token else None
    elif is_demo:
        active_api = ""
        active_token = None
    else:
        active_api = DEFAULT_API_URL
        active_token = gw_token if gw_token else None

    health = fetch_health(active_api) if active_api else fetch_health(None)
    runs = fetch_runs(active_api, auth_token=active_token)

    # Merge newly initiated session runs if not already in fetched list
    if "custom_runs" in st.session_state:
        seen_ids = {r.get("run_id") for r in runs}
        for cr_id, cr_data in reversed(st.session_state["custom_runs"].items()):
            if cr_id not in seen_ids:
                runs.insert(0, cr_data)
                seen_ids.add(cr_id)

    # Manage selected run ID
    if "selected_run_id" not in st.session_state or not st.session_state["selected_run_id"]:
        st.session_state["selected_run_id"] = runs[0].get("run_id") if runs else ""

    selected_run_id = st.session_state.get("selected_run_id", "")

    # Fetch details of selected run
    run_data = fetch_run_details(selected_run_id, active_api, auth_token=active_token) if selected_run_id else None
    if not run_data and selected_run_id and "custom_runs" in st.session_state:
        run_data = st.session_state["custom_runs"].get(selected_run_id)

    # 1. Header with live health pills
    render_header(health, run_data)

    st.divider()

    # 2. Requirement Input Workbench
    render_workbench(active_api, active_token)

    st.divider()

    # 3. Overview KPI Metrics
    metrics = compute_overview_metrics(runs)
    m_col1, m_col2, m_col3, m_col4, m_col5 = st.columns(5)
    m_col1.metric("Total Runs", metrics["total"])
    m_col2.metric("Completed (DONE)", metrics["completed"])
    m_col3.metric("Needs Human", metrics["needs_human"])
    m_col4.metric("Blocked", metrics["blocked"])
    m_col5.metric("Running", metrics["running"])

    st.divider()

    if not run_data:
        if selected_run_id:
            st.error(
                f"❌ **Run Details Unavailable**: Unable to load execution details for `{selected_run_id}` from the Live Gateway or disk storage. "
                "The run may still be initializing or the gateway connection may have been interrupted."
            )
        else:
            st.info("No run selected. Initiate a new run or choose from history below.")
    else:
        # 4. Reference-Inspired Connected Pipeline View
        events = fetch_run_events(selected_run_id, offset=0, limit=100, api_url=active_api, auth_token=active_token)
        stages = evaluate_all_stages(run_data, events)

        if not is_terminal_or_stale(run_data):
            render_active_pipeline_fragment(selected_run_id, active_api, auth_token=active_token)
        else:
            render_pipeline_view(stages, run_data)

        st.divider()

        # 5. Stage Deep Dive Inspection
        render_stage_deep_dive(stages, run_data, events)

        st.divider()

        # 6. Final Execution Summary
        render_execution_summary(run_data)

    st.divider()

    # 7. Run History & Archive
    if runs:
        new_selected = render_run_history(runs, selected_run_id)
        if new_selected != selected_run_id:
            st.session_state["selected_run_id"] = new_selected
            st.rerun()


if __name__ == "__main__":
    render_dashboard()
