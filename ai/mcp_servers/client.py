"""Start every MCP server over stdio and expose their tools as LangChain tools.

    async with MCPToolkit() as toolkit:
        tools = toolkit.tools                       # all servers, for a LangGraph node or agent
        card = await toolkit.call("get_card", card_id=1)   # structured result as a dict

Each server is one child process that stays up for the life of the toolkit (one persistent MCP
session per server), so tool calls do not pay a start-up cost. Children inherit only the
environment variables listed in PASSTHROUGH_ENV, plus anything passed in `env`.
"""

from __future__ import annotations

import os
import sys
from contextlib import AsyncExitStack
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

SERVERS: dict[str, str] = {
    "cards": "ai.mcp_servers.cards_mcp",
    "similarity": "ai.mcp_servers.similarity_mcp",
    "scoring": "ai.mcp_servers.scoring_mcp",
    "comms": "ai.mcp_servers.comms_mcp",
}

# The MCP SDK starts children with a minimal environment, so what they need is passed explicitly.
PASSTHROUGH_ENV = (
    "PATH",
    "HOME",
    "PYTHONPATH",
    "DATABASE_URL",
    "MOCK_AI",
    "RUBRIC_PATH",
    "COMMS_TEMPLATES_PATH",
    "EMBEDDING_MODEL",
    "FASTEMBED_CACHE_DIR",
    "HF_HOME",
    "TMPDIR",
)


def server_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {name: os.environ[name] for name in PASSTHROUGH_ENV if name in os.environ}
    env.update(extra or {})
    return env


def connections(servers: list[str] | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    """LangChain MCP adapter connection settings for each server (run as `python -m <module>`)."""
    chosen = servers or list(SERVERS)
    unknown = sorted(set(chosen) - set(SERVERS))
    if unknown:
        raise ValueError(f"Unknown MCP server(s) {unknown}; known: {sorted(SERVERS)}")
    return {
        name: {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", SERVERS[name]],
            "env": server_env(env),
        }
        for name in chosen
    }


class MCPToolkit:
    """Async context manager that starts the servers and loads their tools.

    handle_tool_errors=False (the default) makes a failing tool raise, which is what a graph node
    wants. Set it to True to hand the error text back to an LLM agent instead.
    """

    def __init__(
        self,
        servers: list[str] | None = None,
        env: dict[str, str] | None = None,
        handle_tool_errors: bool = False,
    ):
        self._connections = connections(servers, env)
        self._handle_errors = handle_tool_errors
        self._stack = AsyncExitStack()
        self.tools_by_server: dict[str, list[BaseTool]] = {}

    async def __aenter__(self) -> MCPToolkit:
        client = MultiServerMCPClient(self._connections)
        try:
            for name in self._connections:
                session = await self._stack.enter_async_context(client.session(name))
                self.tools_by_server[name] = await load_mcp_tools(
                    session, server_name=name, handle_tool_errors=self._handle_errors
                )
        except BaseException:
            await self._stack.aclose()
            raise
        names = [t.name for tools in self.tools_by_server.values() for t in tools]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            await self._stack.aclose()
            raise RuntimeError(f"Tool names must be unique across servers; duplicated: {duplicates}")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._stack.aclose()

    @property
    def tools(self) -> list[BaseTool]:
        return [tool for tools in self.tools_by_server.values() for tool in tools]

    async def call(self, name: str, **arguments: Any) -> Any:
        """Call a tool and return its structured result (a dict), or its text if it has none.

        Raises langchain's ToolException if the tool reports an error (unless the toolkit was
        created with handle_tool_errors=True).
        """
        message = await self.tool(name).ainvoke(
            {"type": "tool_call", "name": name, "args": arguments, "id": f"call-{name}"}
        )
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        if artifact.get("structured_content") is not None:
            return artifact["structured_content"]
        content = message.content
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content

    def tool(self, name: str) -> BaseTool:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(f"No tool named '{name}'. Available: {sorted(t.name for t in self.tools)}")
