from __future__ import annotations

import json
import subprocess
import sys

import pytest
from langchain_core.tools import ToolException

from ai.mcp_servers.client import PASSTHROUGH_ENV, SERVERS, MCPToolkit, connections, server_env
from tests.mcp.conftest import open_server

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {
    "cards": {
        "get_card", "list_cards", "get_card_context", "list_problems", "update_card_fields",
        "add_comment", "add_tag", "link_cards", "attach_problem", "save_suggestion",
    },
    "similarity": {"index_card", "index_board", "find_similar_cards"},
    "scoring": {"get_rubric", "save_scores", "compute_priority"},
    "comms": {"save_draft", "list_drafts", "get_templates"},
}  # fmt: skip


# ---- the client: LangChain tools over persistent stdio sessions -------------------------------
async def test_toolkit_starts_all_servers_and_returns_langchain_tools(db):
    async with MCPToolkit(env=db.env) as toolkit:
        assert {server: {t.name for t in tools} for server, tools in toolkit.tools_by_server.items()} == EXPECTED_TOOLS
        assert len(toolkit.tools) == 19
        for tool in toolkit.tools:
            assert tool.description and isinstance(tool.args_schema, dict)
        with pytest.raises(KeyError, match="No tool named 'nope'"):
            toolkit.tool("nope")


async def test_call_returns_structured_results(db):
    ids = db.ids
    async with MCPToolkit(env=db.env) as toolkit:
        card = await toolkit.call("get_card", card_id=ids["saml"])
        assert card["title"] == "Support SAML single sign-on"

        await toolkit.call("index_board", board_id=ids["board"])
        similar = await toolkit.call("find_similar_cards", card_id=ids["saml"], top_k=1)
        assert similar["results"][0]["card_id"] == ids["okta"]

        suggestion = await toolkit.call("add_tag", card_id=ids["okta"], tag="security", reason="Sign-on.")
        assert suggestion["status"] == "pending" and suggestion["applied"] is False

        ratings = {"customer_impact": 5, "demand_breadth": 5, "strategic_fit": 5, "effort_fit": 5}
        assert (await toolkit.call("compute_priority", ratings=ratings))["total"] == 100.0
        assert (await toolkit.call("get_templates", audience="sales"))["templates"]


async def test_tools_also_work_as_plain_langchain_tools(db):
    """What an agent or ToolNode sees: text content blocks holding the JSON result."""
    async with MCPToolkit(servers=["cards"], env=db.env) as toolkit:
        content = await toolkit.tool("get_card").ainvoke({"card_id": db.ids["dark"]})
    assert json.loads(content[0]["text"])["title"] == "Dark mode"


async def test_one_toolkit_serves_many_calls_from_the_same_sessions(db):
    async with MCPToolkit(env=db.env) as toolkit:
        get_card = toolkit.tool("get_card")
        titles = [(await toolkit.call("get_card", card_id=db.ids[k]))["title"] for k in ("saml", "dark", "csv", "saml")]
        assert get_card.name == "get_card"
    assert titles == [
        "Support SAML single sign-on",
        "Dark mode",
        "Export reports to CSV",
        "Support SAML single sign-on",
    ]


async def test_tool_errors_raise_by_default_and_can_be_returned_as_text(db):
    async with MCPToolkit(servers=["cards"], env=db.env) as toolkit:
        with pytest.raises(ToolException, match="Card 999 not found"):
            await toolkit.tool("get_card").ainvoke({"card_id": 999})
        with pytest.raises(ToolException, match="Card 999 not found"):
            await toolkit.call("get_card", card_id=999)
    async with MCPToolkit(servers=["cards"], env=db.env, handle_tool_errors=True) as toolkit:
        text = await toolkit.tool("get_card").ainvoke({"card_id": 999})
    assert "Card 999 not found" in str(text)


async def test_a_subset_of_servers_can_be_started(db):
    async with MCPToolkit(servers=["scoring", "comms"], env=db.env) as toolkit:
        assert set(toolkit.tools_by_server) == {"scoring", "comms"}


def test_unknown_server_name_is_rejected():
    with pytest.raises(ValueError, match="Unknown MCP server"):
        connections(["cards", "payments"])


def test_child_processes_only_get_the_listed_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-secret-2")
    monkeypatch.setenv("SOMETHING_ELSE", "x")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    env = server_env({"RUBRIC_PATH": "/tmp/r.yaml"})
    assert env["DATABASE_URL"] == "sqlite:///x.db" and env["RUBRIC_PATH"] == "/tmp/r.yaml"
    assert set(env) <= set(PASSTHROUGH_ENV) | {"RUBRIC_PATH"}
    assert "ANTHROPIC_API_KEY" not in env and "LITELLM_API_KEY" not in env and "SOMETHING_ELSE" not in env
    for name, settings in connections().items():
        assert settings["transport"] == "stdio" and settings["args"] == ["-m", SERVERS[name]]


# ---- each server on its own -------------------------------------------------------------------
@pytest.mark.parametrize("server", sorted(EXPECTED_TOOLS))
def test_each_server_runs_standalone(server):
    done = subprocess.run(
        [sys.executable, "-m", SERVERS[server], "--selftest"], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    name, _, listed = done.stdout.strip().partition(": ")
    assert name == f"{server}_mcp"
    assert set(listed.split(", ")) == EXPECTED_TOOLS[server]


def test_a_server_keeps_stdout_clean_for_the_protocol(db):
    """Anything printed to stdout besides JSON-RPC would corrupt the stdio stream."""
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})  # fmt: skip
    done = subprocess.run(
        [sys.executable, "-m", SERVERS["cards"]],
        input=request + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        env=db.env,
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    assert lines and all(json.loads(line)["jsonrpc"] == "2.0" for line in lines)


def test_native_writes_to_stdout_cannot_corrupt_the_protocol():
    """Libraries that write to fd 1 directly (model downloaders) must land on stderr, not the protocol."""
    code = (
        "import os, sys\n"
        "from ai.mcp_servers.common import reserve_stdout_for_protocol\n"
        "reserve_stdout_for_protocol()\n"
        "os.write(1, b'NATIVE NOISE\\n')\n"
        "print('protocol line')\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert done.stdout == "protocol line\n"
    assert "NATIVE NOISE" in done.stderr


def test_the_list_script_prints_every_tool():
    done = subprocess.run(
        [sys.executable, "-m", "ai.mcp_servers.list_tools"], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert "4 MCP servers, 19 tools" in done.stdout
    for tools in EXPECTED_TOOLS.values():
        for name in tools:
            assert f"  {name}(" in done.stdout


def test_the_list_script_can_print_json():
    done = subprocess.run(
        [sys.executable, "-m", "ai.mcp_servers.list_tools", "--json"], capture_output=True, text=True, timeout=120
    )
    catalog = json.loads(done.stdout)
    assert {s: {t["name"] for t in tools} for s, tools in catalog.items()} == EXPECTED_TOOLS
    link = next(t for t in catalog["cards"] if t["name"] == "link_cards")
    assert [p["name"] for p in link["params"] if p["required"]] == [
        "card_id",
        "other_card_id",
        "type",
        "confidence",
        "rationale",
    ]


# ---- rules the servers must follow ------------------------------------------------------------
def test_servers_import_core_but_never_the_api_or_any_llm_code():
    code = (
        "import sys\n"
        "import ai.mcp_servers.cards_mcp.server, ai.mcp_servers.similarity_mcp.server\n"
        "import ai.mcp_servers.scoring_mcp.server, ai.mcp_servers.comms_mcp.server\n"
        "names = ('fastapi', 'apps', 'anthropic', 'langchain_anthropic', 'langchain_core')\n"
        "prefixes = ('apps.', 'ai.llm', 'langchain_anthropic.', 'anthropic.')\n"
        "bad = sorted(m for m in sys.modules if m in names or m.startswith(prefixes))\n"
        "assert 'core.services.cards' in sys.modules\n"
        "print('bad:', bad)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "bad: []"


async def test_every_tool_is_documented_and_typed(db):
    """Clear descriptions, described and typed inputs, and a declared output for every tool."""
    seen = 0
    for module in SERVERS.values():
        async with open_server(module, db.env) as session:
            for tool in (await session.list_tools()).tools:
                seen += 1
                assert tool.description and len(tool.description) >= 30, tool.name
                assert tool.outputSchema, f"{tool.name} has no output schema"
                for name, spec in tool.inputSchema.get("properties", {}).items():
                    # Optional parameters put their description inside the anyOf branch that holds the type.
                    described = spec.get("description") or any(b.get("description") for b in spec.get("anyOf", []))
                    assert described, f"{tool.name}.{name} has no description"
                    assert any(k in spec for k in ("type", "anyOf", "$ref", "enum", "allOf")), (
                        f"{tool.name}.{name} is untyped"
                    )
    assert seen == 19


async def test_tools_that_propose_say_so_and_cannot_be_told_to_apply(db):
    async with open_server(SERVERS["cards"], db.env) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
    for name in ("update_card_fields", "add_comment", "add_tag", "link_cards", "attach_problem"):
        assert "PROPOSE" in tools[name].description, name
    for name in ("link_cards", "attach_problem"):
        status = tools[name].inputSchema["properties"]["status"]
        assert status.get("const") == "suggested" or status.get("enum") == ["suggested"]  # only one value is accepted
