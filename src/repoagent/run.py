"""RepoAgent CLI entry point.

Usage:
    python -m repoagent.run --repo <path> --requirement '<text>'
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys

from repoagent.flow import RepoAgentFlow
from repoagent.providers import get_provider
from repoagent.schemas import RunState


def build_parser() -> argparse.ArgumentParser:
    """Construct argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m repoagent.run",
        description="RepoAgent: Automated requirement-to-PR pipeline",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default="demo_repo",
        help="Path or URL to the repository (default: demo_repo)",
    )
    parser.add_argument(
        "--requirement",
        type=str,
        required=True,
        help="Natural-language requirement to implement",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="LLM provider: 'gemini' or 'openai_compatible' (overrides REPOAGENT_PROVIDER)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Maximum correction retry iterations on test failures (default: 3)",
    )
    parser.add_argument(
        "--simulate-failures",
        type=int,
        default=0,
        help="Simulate test failures for N iterations (for testing retry cap)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Main execution function."""
    # Ensure stdout handles UTF-8 output
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(argv)

    # Set provider if explicitly specified
    if args.provider:
        os.environ["REPOAGENT_PROVIDER"] = args.provider

    # Validate provider configuration
    _ = get_provider()

    # Initialize RunState
    initial_state = RunState(
        requirement=args.requirement,
        repo_url=args.repo,
        max_retries=args.max_retries,
    )

    # Execute orchestrator flow
    flow = RepoAgentFlow(
        initial_state=initial_state,
        simulate_test_failures=args.simulate_failures,
    )
    _ = flow.kickoff()

    # Print final RunState as formatted JSON
    print(flow.state.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
