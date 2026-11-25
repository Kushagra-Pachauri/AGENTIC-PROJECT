"""RepoAgent CLI entry point.

Usage:
    python -m repoagent.run --repo <path> --requirement '<text>'
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

from repoagent.flow import RepoAgentFlow
from repoagent.providers import get_provider
from repoagent.schemas import RunState, RunStatus
from repoagent.workspace_utils import get_runs_dir, setup_workspace
from dotenv import load_dotenv

load_dotenv()


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
        default=None,
        help="Natural-language requirement to implement (required unless --from-run is specified)",
    )
    parser.add_argument(
        "--from-run",
        type=str,
        default=None,
        help="Promote an existing completed run's workspace to a Pull Request",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="LLM provider: 'gemini' or 'openai_compatible' (overrides REPOAGENT_PROVIDER)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="LLM model name (overrides REPOAGENT_MODEL)",
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
    parser.add_argument(
        "--live",
        dest="use_live_agents",
        action="store_true",
        default=True,
        help="Use live agents with LLM (default: True)",
    )
    parser.add_argument(
        "--stub",
        dest="use_live_agents",
        action="store_false",
        help="Use deterministic stubs instead of live agents",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        default=False,
        help="Run in replay mode: apply reference ChangeSet, test runner and Reviewer",
    )
    parser.add_argument(
        "--create-pr",
        action="store_true",
        default=False,
        help="Open a real GitHub Pull Request if verification succeeds",
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

    # 1. Standalone promotion workflow
    if args.from_run:
        from repoagent.monitoring import EventType, get_event_logger
        from repoagent.pr_agent import promote_existing_run

        event_logger = get_event_logger()
        event_logger.log_event(
            run_id=args.from_run,
            event_type=EventType.TASK_STARTED,
            stage="promote",
            input_summary={"from_run": args.from_run, "live_pr": args.create_pr},
        )
        try:
            state = promote_existing_run(
                run_id=args.from_run,
                live_pr=args.create_pr,
            )
            event_logger.log_event(
                run_id=args.from_run,
                event_type=EventType.TASK_COMPLETED,
                stage="promote",
                output_summary={
                    "status": state.status.value,
                    "pr_url": state.pr.url if state.pr else None,
                },
            )
            print(state.model_dump_json(indent=2))
            return 0 if state.status == RunStatus.DONE else 1
        except Exception as exc:
            event_logger.log_event(
                run_id=args.from_run,
                event_type=EventType.ERROR,
                stage="promote",
                error_details=str(exc),
            )
            print(f"[ERROR] Failed to promote run '{args.from_run}': {exc}", file=sys.stderr)
            return 1

    if not args.requirement:
        parser.error("--requirement is required unless --from-run is specified.")

    if args.replay:
        from repoagent.flow import REGISTERED_REPLAY_BENCHMARKS

        if args.requirement not in REGISTERED_REPLAY_BENCHMARKS:
            parser.error(
                f"REPLAY mode rejected: requirement '{args.requirement}' does not match "
                f"registered reference benchmark. REPLAY mode is strictly reserved for "
                f"pre-recorded reference solutions. For custom requirements, omit --replay."
            )

    # Set provider and model if explicitly specified
    if args.provider:
        os.environ["REPOAGENT_PROVIDER"] = args.provider
    if args.model:
        os.environ["REPOAGENT_MODEL"] = args.model

    # Validate provider configuration
    _ = get_provider()

    # Initialize RunState
    import uuid

    run_id = f"replay_{uuid.uuid4().hex[:8]}" if args.replay else uuid.uuid4().hex[:12]
    initial_state = RunState(
        run_id=run_id,
        requirement=args.requirement,
        repo_url=args.repo,
        max_retries=args.max_retries,
        is_replay=args.replay,
    )

    # Always isolate target repository into a fresh workspace under runs/<run_id>/workspace
    run_dir = get_runs_dir() / initial_state.run_id
    isolated_workspace = run_dir / "workspace"
    source_repo = Path(args.repo).resolve()
    setup_workspace(source_repo, isolated_workspace)

    initial_state.repo_url = str(isolated_workspace)

    # Execute orchestrator flow on the isolated workspace
    flow = RepoAgentFlow(
        initial_state=initial_state,
        simulate_test_failures=args.simulate_failures,
        use_live_agents=args.use_live_agents,
        create_pr=args.create_pr,
        workspace=isolated_workspace,
    )
    _ = flow.kickoff()

    # Print final RunState as formatted JSON
    print(flow.state.model_dump_json(indent=2))
    return 0 if flow.state.status == RunStatus.DONE else 1


if __name__ == "__main__":
    sys.exit(main())
