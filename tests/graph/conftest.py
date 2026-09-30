"""Fixtures for graph tests: the temp SQLite database from the MCP tests, real MCP server processes,
and the mock model, so a run exercises every layer except a real LLM."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from ai.graph.state import GraphConfig
from ai.graph.workflow import Workflow
from ai.llm.factory import LLMResult
from ai.llm.mock import MockLLM
from core.models import AIRun, Card, RequesterType
from core.services.cards import CardService
from tests.mcp.conftest import Db, anyio_backend, db  # noqa: F401  (fixtures re-exported for this package)

# The mock embedder only matches shared words, so candidates are found with a low floor.
TEST_CONFIG = GraphConfig(min_similarity=0.05)

VAGUE = ("Improve it", "Better.")
DISTINCT = (
    "Offline mode for field technicians",
    "Technicians work in basements without signal; security rules forbid cloud exports, "
    "so records must be editable offline.",
)
# Nearly the same words as the fixture's "saml" card, so the mock judge calls it a duplicate.
DUPLICATE_OF_SAML = ("Support SAML single sign-on please", "IT requires SAML sign-on before we roll the product out.")


class ScriptedLLM(MockLLM):
    """The mock model, except for the prompts you give a hook: return a dict or model, or raise."""

    def __init__(self, hooks: dict[str, Callable[[Any], dict | BaseModel]] | None = None):
        self.hooks = hooks or {}
        self.calls: list[str] = []

    def invoke(self, prompt):
        self.calls.append(prompt.name)
        hook = self.hooks.get(prompt.name)
        if hook is None:
            return super().invoke(prompt)
        out = hook(prompt)
        return LLMResult(prompt.output_schema.model_validate(out) if isinstance(out, dict) else out)


def add_card(db: Db, title: str, description: str) -> int:
    with db.session() as session:
        card = CardService(session).create_card(
            board_id=db.ids["board"], title=title, description=description, requester_type=RequesterType.customer
        )
        session.commit()
        return card.id


def card_snapshot(db: Db, card_id: int) -> tuple:
    with db.session() as session:
        card = session.get(Card, card_id)
        return (
            {c.name: getattr(card, c.name) for c in Card.__table__.columns},
            sorted(t.id for t in card.tags),
            card.comment_count,
            card.vote_count,
        )


def runs(db: Db, **filters: Any) -> list[AIRun]:
    with db.session() as session:
        return list(session.scalars(select(AIRun).filter_by(**filters).order_by(AIRun.id)))


def step_rows(db: Db, run_id: int) -> dict[str, AIRun]:
    """The step rows of a workflow run, by step name (the last one wins for repeated steps)."""
    return {r.kind.removeprefix("step:"): r for r in runs(db, parent_run_id=run_id) if r.kind.startswith("step:")}


@pytest.fixture
def make_workflow(db, tmp_path):
    """Workflow factory: `async with make_workflow(...) as wf`. Pass the same checkpoint to share state."""

    def make(*, llm=None, config: GraphConfig | None = None, checkpoint=None) -> Workflow:
        return Workflow(
            session_factory=db.factory,
            env=db.env,
            llm=llm or MockLLM(),
            config=config or TEST_CONFIG,
            checkpoint_path=checkpoint or tmp_path / "checkpoints.db",
        )

    return make
