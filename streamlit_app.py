"""Streamlit Community Cloud Entry Point for RepoAgent Execution Dashboard.

Runs standalone on Streamlit Community Cloud with zero required secrets,
reading verified runs from showcase_data/ and presenting full 11-stage pipeline,
Docker test evidence, ADK Reviewer reports, and real GitHub PR #7.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure src/ is on sys.path
root_dir = Path(__file__).resolve().parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# Enable demo mode automatically if no local backend API is specified
if "REPOAGENT_API_URL" not in os.environ:
    os.environ["REPOAGENT_DEMO_MODE"] = "true"

from repoagent.ui.dashboard import render_dashboard

if __name__ == "__main__":
    render_dashboard()
