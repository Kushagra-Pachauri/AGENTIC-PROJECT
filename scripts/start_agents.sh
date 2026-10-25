#!/usr/bin/env bash
# Start RepoAgent Google ADK A2A Agents (Planner on 8001, Reviewer on 8002)
set -euo pipefail

PLANNER_PORT="${REPOAGENT_PLANNER_PORT:-8001}"
REVIEWER_PORT="${REPOAGENT_REVIEWER_PORT:-8002}"
HOST="${REPOAGENT_A2A_HOST:-127.0.0.1}"
WORKSPACE="${REPOAGENT_WORKSPACE:-demo_repo}"

echo "=================================================="
echo "Starting RepoAgent ADK Agents over A2A"
echo "  Planner Agent:   http://${HOST}:${PLANNER_PORT}"
echo "  Reviewer Agent:  http://${HOST}:${REVIEWER_PORT}"
echo "  Target Repo:     ${WORKSPACE}"
echo "=================================================="

# Determine Python command
PYTHON="python"
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -f ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
fi

# Run both agents via server module
exec "$PYTHON" -m repoagent.agents.adk.server \
    --host "$HOST" \
    --planner-port "$PLANNER_PORT" \
    --reviewer-port "$REVIEWER_PORT" \
    --workspace "$WORKSPACE"
