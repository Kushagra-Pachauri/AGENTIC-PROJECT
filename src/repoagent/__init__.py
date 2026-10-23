"""RepoAgent - Agentic AI system for GitHub PRs."""

from __future__ import annotations

import contextlib
import sys

# Ensure UTF-8 output encoding across Windows consoles for CrewAI events
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stderr.reconfigure(encoding="utf-8")
