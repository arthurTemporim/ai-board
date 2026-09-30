"""Fixtures for MCP tests: a temp SQLite file, seeded with a few cards, and real server processes.

Servers run as child processes (stdio), so the database must be a file and its URL is handed over
through the environment. Tests talk to them with the official MCP client, like a real caller.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy.orm import Session, sessionmaker

import core.models  # noqa: F401
from ai.mcp_servers.client import server_env
from core.db import Base, make_engine, make_session_factory
from core.models import AIRun, LinkStatus, Problem, RequesterType, User
from core.services.boards import BoardService
from core.services.cards import CardService
from core.services.engagement import CommentService, FeedbackService, VoteService
from core.services.tags import TagService

CARDS = {
    "saml": ("Support SAML single sign-on", "IT requires SAML sign-on before we roll the product out."),
    "okta": ("Single sign-on with Okta for our staff", "We want SAML single sign-on so staff use one login."),
    "dark": ("Dark mode", "A night theme for operators on late shifts."),
    "csv": ("Export reports to CSV", "Download report tables as CSV files."),
}


@dataclass
class Db:
    url: str
    factory: sessionmaker[Session]
    ids: dict[str, int] = field(default_factory=dict)
    # Holds LITELLM_API_KEY when it is set in the environment: never show it in a failure report.
    env: dict[str, str] = field(default_factory=dict, repr=False)

    def session(self) -> Session:
        return self.factory()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def db(tmp_path) -> Db:
    url = f"sqlite:///{tmp_path / 'mcp.db'}"
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    ids: dict[str, int] = {}
    with factory() as session:
        alice = User(name="Alice", role="pm")
        session.add(alice)
        session.flush()
        ids["alice"] = alice.id

        boards = BoardService(session)
        board_a = boards.create_board("Core")
        board_b = boards.create_board("Other")
        ids["board"], ids["other_board"] = board_a.id, board_b.id
        ids["inbox"] = board_a.columns[0].id
        ids["triaged"] = board_a.columns[1].id

        tags = TagService(session)
        ids["tag_security"] = tags.create_tag(board_a.id, "security", "#DC2626").id
        ids["tag_ux"] = tags.create_tag(board_a.id, "ux", "#DB2777").id

        cards = CardService(session)
        for key, (title, description) in CARDS.items():
            card = cards.create_card(
                board_id=board_a.id,
                title=title,
                description=description,
                requester_type=RequesterType.customer,
                tag_ids=[ids["tag_security"]] if key == "saml" else [],
            )
            ids[key] = card.id
        ids["foreign"] = cards.create_card(
            board_id=board_b.id, title="Support SAML single sign-on", description="Same words, other board."
        ).id

        CommentService(session).add_comment(ids["saml"], alice.id, "Top blocker in three deals.")
        FeedbackService(session).add_feedback(ids["saml"], "Sales call", "We cannot sign without SSO.", "Northwind")
        VoteService(session).add_vote(ids["saml"], alice.id)
        problem = Problem(
            board_id=board_a.id, title="Enterprise security gaps", statement="Buyers cannot meet security needs."
        )
        other_problem = Problem(board_id=board_b.id, title="Other board problem", statement="")
        session.add_all([problem, other_problem])
        session.flush()
        ids["problem"], ids["other_problem"] = problem.id, other_problem.id
        run = AIRun(kind="test")
        session.add(run)
        session.flush()
        ids["run"] = run.id
        session.commit()
    return Db(url, factory, ids, server_env({"DATABASE_URL": url, "MOCK_AI": "1"}))


@asynccontextmanager
async def open_server(module: str, env: dict[str, str]) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(command=sys.executable, args=["-m", module], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


class Tools:
    """Thin helper around a ClientSession: call a tool and get its structured result or its error text."""

    def __init__(self, session: ClientSession):
        self.session = session

    async def ok(self, tool: str, **arguments: Any) -> dict:
        result = await self.session.call_tool(tool, arguments)
        assert not result.isError, f"{tool} failed: {result.content}"
        assert result.structuredContent is not None, f"{tool} returned no structured content"
        return result.structuredContent

    async def error(self, tool: str, **arguments: Any) -> str:
        result = await self.session.call_tool(tool, arguments)
        assert result.isError, f"{tool} unexpectedly succeeded: {result.structuredContent}"
        return " ".join(getattr(part, "text", "") for part in result.content)


@asynccontextmanager
async def tools_for(module: str, db: Db) -> AsyncIterator[Tools]:
    async with open_server(module, db.env) as session:
        yield Tools(session)


CARDS_MODULE = "ai.mcp_servers.cards_mcp"
SIMILARITY_MODULE = "ai.mcp_servers.similarity_mcp"
SCORING_MODULE = "ai.mcp_servers.scoring_mcp"
COMMS_MODULE = "ai.mcp_servers.comms_mcp"

assert LinkStatus.suggested  # imported for tests that flip a link to confirmed
