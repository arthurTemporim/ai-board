"""MCP servers (official Python SDK, FastMCP, stdio). Deterministic tools only: no LLM calls here.

Each server is a package with `server.py` (the tools) and `__main__.py`, so it runs standalone:

    python -m ai.mcp_servers.cards_mcp          # serve over stdio
    python -m ai.mcp_servers.cards_mcp --selftest   # print the tool names and exit

Servers import `core` (models and services) and never the FastAPI app. They read the database
from DATABASE_URL, so the process that spawns them must pass it on (see client.py).
"""
