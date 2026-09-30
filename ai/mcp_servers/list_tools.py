"""List every tool of every MCP server: `python -m ai.mcp_servers.list_tools [--json]`."""

from __future__ import annotations

import asyncio
import json
import sys

from ai.mcp_servers.client import MCPToolkit


def _summary(tool) -> dict:
    schema = tool.args_schema if isinstance(tool.args_schema, dict) else {}
    required = set(schema.get("required", []))
    params = [
        {"name": name, "type": _type(spec), "required": name in required, "description": spec.get("description", "")}
        for name, spec in schema.get("properties", {}).items()
    ]
    return {"name": tool.name, "description": tool.description, "params": params}


def _type(spec: dict) -> str:
    if "type" in spec:
        return str(spec["type"])
    options = spec.get("anyOf") or spec.get("oneOf") or []
    names = [o.get("type") or (o.get("$ref", "").split("/")[-1] or "object") for o in options]
    if "$ref" in spec:
        return str(spec["$ref"]).split("/")[-1]
    return " | ".join(n for n in names if n) or "any"


async def collect() -> dict[str, list[dict]]:
    async with MCPToolkit() as toolkit:
        return {server: [_summary(t) for t in tools] for server, tools in toolkit.tools_by_server.items()}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    catalog = asyncio.run(collect())
    if "--json" in args:
        print(json.dumps(catalog, indent=2))
        return 0
    total = sum(len(tools) for tools in catalog.values())
    print(f"{len(catalog)} MCP servers, {total} tools\n")
    for server, tools in catalog.items():
        print(f"{server}_mcp ({len(tools)} tools)")
        for tool in tools:
            params = ", ".join(f"{p['name']}{'' if p['required'] else '?'}: {p['type']}" for p in tool["params"])
            print(f"  {tool['name']}({params})")
            print(f"      {tool['description'].split('. ')[0].rstrip('.')}.")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
