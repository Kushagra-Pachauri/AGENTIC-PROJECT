"""MCP Client helpers — attach MCP servers as tools to CrewAI and ADK agents.

This module provides:
  - ``get_crewai_tools``: connect to an MCP server and return CrewAI ``BaseTool`` instances
  - ``get_adk_toolset``: wrap an MCP server as an ADK ``MCPToolset``
  - ``get_repo_reader_params``: build StdioServerParameters for the repo reader
  - ``get_test_runner_params``: build StdioServerParameters for the test runner
  - ``get_github_params``: build StdioServerParameters for the GitHub MCP server
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from mcp.client.stdio import StdioServerParameters

if TYPE_CHECKING:
    from crewai.tools.base_tool import BaseTool


# ---------------------------------------------------------------------------
# Server parameter builders
# ---------------------------------------------------------------------------


def _python_exe() -> str:
    """Return the path to the current or workspace virtualenv Python interpreter."""
    venv_py = Path(".venv/Scripts/python.exe").resolve()
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def _server_module_path(module_name: str) -> str:
    """Return the absolute path to a server module inside mcp_servers/."""
    server_dir = Path(__file__).resolve().parent / "mcp_servers"
    path = server_dir / f"{module_name}.py"
    if not path.is_file():
        msg = f"MCP server module not found: {path}"
        raise FileNotFoundError(msg)
    return str(path)


def get_repo_reader_params(workspace: str | Path) -> StdioServerParameters:
    """Build ``StdioServerParameters`` for the Repo Reader MCP server.

    Args:
        workspace: path to the repository root to scope the server to.

    Returns:
        Parameters ready to pass to CrewAI ``MCPServerAdapter`` or ADK ``MCPToolset``.
    """
    return StdioServerParameters(
        command=_python_exe(),
        args=[_server_module_path("repo_reader")],
        env={
            **{k: v for k, v in os.environ.items()},
            "REPOAGENT_WORKSPACE": str(Path(workspace).resolve()),
        },
    )


def get_test_runner_params(workspace: str | Path) -> StdioServerParameters:
    """Build ``StdioServerParameters`` for the Test Runner MCP server.

    Args:
        workspace: path to the repository root to mount in Docker.

    Returns:
        Parameters ready to pass to CrewAI ``MCPServerAdapter`` or ADK ``MCPToolset``.
    """
    env = {
        **{k: v for k, v in os.environ.items()},
        "REPOAGENT_WORKSPACE": str(Path(workspace).resolve()),
    }
    # Forward sandbox config from env
    for key in ("SANDBOX_IMAGE", "SANDBOX_TIMEOUT_S", "SANDBOX_CPU_LIMIT", "SANDBOX_MEMORY_LIMIT"):
        if key in os.environ:
            env[key] = os.environ[key]

    return StdioServerParameters(
        command=_python_exe(),
        args=[_server_module_path("test_runner")],
        env=env,
    )


def get_github_params() -> StdioServerParameters:
    """Build ``StdioServerParameters`` for the GitHub MCP server (npm package).

    Requires ``GITHUB_TOKEN`` in the environment (or ``GITHUB_PERSONAL_ACCESS_TOKEN``).

    Returns:
        Parameters for ``@modelcontextprotocol/server-github`` via npx.
    """
    from dotenv import load_dotenv

    load_dotenv("/app/.env")
    load_dotenv()

    token = os.environ.get("GITHUB_TOKEN", os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", ""))
    if not token:
        msg = "GITHUB_TOKEN environment variable is required for the GitHub MCP server."
        raise OSError(msg)

    npx_cmd = shutil.which("npx") or "npx"
    return StdioServerParameters(
        command=npx_cmd,
        args=["-y", "@modelcontextprotocol/server-github"],
        env={
            **{k: v for k, v in os.environ.items()},
            "GITHUB_PERSONAL_ACCESS_TOKEN": token,
        },
    )


# ---------------------------------------------------------------------------
# CrewAI integration
# ---------------------------------------------------------------------------


def get_crewai_mcp_server(server_params: StdioServerParameters) -> Any:
    """Convert StdioServerParameters to CrewAI's MCPServerStdio configuration.

    Can be passed directly into CrewAI's ``Agent(mcps=[...])``.
    """
    from crewai.mcp.config import MCPServerStdio

    return MCPServerStdio(
        command=server_params.command,
        args=list(server_params.args),
        env=dict(server_params.env) if server_params.env else None,
    )


def get_crewai_tools(server_params: StdioServerParameters) -> list[BaseTool]:
    """Connect to an MCP server and return CrewAI-compatible tool instances.

    Discovers exposed tools from the MCP server and wraps them as CrewAI
    ``BaseTool`` objects for agents using ``tools=[...]``.

    Args:
        server_params: stdio connection parameters for the MCP server.

    Returns:
        List of CrewAI ``BaseTool`` instances ready for an Agent's tools list.
    """
    import asyncio

    from crewai.tools import tool

    discovered = asyncio.run(list_tools_direct(server_params))
    tools: list[BaseTool] = []
    for info in discovered:
        t_name = info["name"]
        t_desc = info["description"] or f"MCP tool {t_name}"

        if t_name == "list_tree":
            def list_tree(path: str = ".", max_depth: int = 4) -> str:
                """List the directory tree rooted at path (relative to workspace)."""
                return asyncio.run(call_tool_direct(server_params, "list_tree", {"path": path, "max_depth": max_depth}))

            tools.append(tool(list_tree))
        elif t_name == "read_file":
            def read_file(path: str, offset: int = 0, limit: int = 200) -> str:
                """Read text file contents from offset to offset + limit.

                Parameters:
                - path: Relative path to the file in workspace (required).
                - offset: 0-indexed line number to start reading from (default: 0).
                - limit: Maximum number of lines to return (default: 200).

                DO NOT use line_start or line_end. Use offset and limit.
                """
                return asyncio.run(call_tool_direct(server_params, "read_file", {"path": path, "offset": offset, "limit": limit}))

            tools.append(tool(read_file))
        elif t_name == "search_code":
            def search_code(pattern: str, path: str = ".") -> str:
                """Search for a regex pattern in files under path (relative to workspace)."""
                return asyncio.run(call_tool_direct(server_params, "search_code", {"pattern": pattern, "path": path}))

            tools.append(tool(search_code))
        elif t_name == "write_file":
            def write_file(path: str, content: str) -> str:
                """Write text content to a file at path (relative to workspace)."""
                return asyncio.run(call_tool_direct(server_params, "write_file", {"path": path, "content": content}))

            tools.append(tool(write_file))
        else:
            def _make_runner(name: str) -> Any:
                def _runner(**kwargs: Any) -> str:
                    """Generic MCP tool runner."""
                    return asyncio.run(call_tool_direct(server_params, name, kwargs))

                return _runner

            fn = _make_runner(t_name)
            fn.__name__ = t_name
            fn.__doc__ = t_desc
            tools.append(tool(fn))

    return tools


# ---------------------------------------------------------------------------
# ADK integration
# ---------------------------------------------------------------------------


def get_adk_toolset(server_params: StdioServerParameters) -> Any:
    """Wrap an MCP server as an ADK ``McpToolset``.

    The returned toolset can be passed directly in an ADK Agent's ``tools`` list.
    ADK handles the lifecycle (connection, tool discovery, teardown) internally.

    Args:
        server_params: stdio connection parameters for the MCP server.

    Returns:
        An ADK ``McpToolset`` instance.
    """
    from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams

    try:
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset as _McpToolset
    except ImportError:
        from google.adk.tools.mcp_tool.mcp_toolset import (
            MCPToolset as _McpToolset,  # type: ignore[no-redef]
        )

    return _McpToolset(connection_params=StdioConnectionParams(server_params=server_params))


# ---------------------------------------------------------------------------
# Direct MCP client (for testing / scripts — bypasses CrewAI / ADK)
# ---------------------------------------------------------------------------


async def call_tool_direct(
    server_params: StdioServerParameters,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> str:
    """Call a single MCP tool directly using the raw MCP client.

    Useful for testing and demo scripts where we don't need an agent wrapper.

    Args:
        server_params: stdio parameters for the MCP server.
        tool_name: name of the tool to call.
        arguments: keyword arguments for the tool.

    Returns:
        The text content of the tool result.
    """
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(tool_name, arguments or {})
        # Concatenate text content from all result parts
        parts: list[str] = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
        return "\n".join(parts)


async def list_tools_direct(
    server_params: StdioServerParameters,
) -> list[dict[str, str]]:
    """List all tools exposed by an MCP server.

    Returns:
        List of dicts with ``name`` and ``description`` keys.
    """
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools_result = await session.list_tools()
        return [{"name": t.name, "description": t.description or ""} for t in tools_result.tools]
