# RepoAgent Sandbox — isolated environment for running user-generated code.
# Non-root user, no network. CPU/memory/time limits enforced by Docker run flags.

FROM python:3.11-slim

# Install common dev tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Install Python linting / testing tools available inside sandbox
RUN pip install --no-cache-dir \
        pytest==8.4.1 \
        ruff==0.11.13 \
        pydantic==2.12.5

# Create non-root user
RUN groupadd -r sandbox && useradd -r -g sandbox -m sandbox

# Workspace mount point
RUN mkdir -p /workspace && chown sandbox:sandbox /workspace
WORKDIR /workspace

USER sandbox
