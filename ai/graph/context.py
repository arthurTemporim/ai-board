"""What every node shares: the MCP tools, the LLM, and the AIRun bookkeeping.

All AIRun writes here are short transactions that commit immediately. The graph process never
keeps a write transaction open while it waits for a model or a tool, because the MCP servers write
to the same SQLite file from other processes and would otherwise hit "database is locked".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from ai.graph.state import GraphConfig
from ai.llm.factory import StructuredLLM
from ai.llm.registry import PromptRegistry
from ai.llm.runner import RunResult, run_prompt_logged
from ai.mcp_servers.client import MCPToolkit
from core.db import session_scope
from core.models import AIRun, Card, RunStatus
from core.models.types import utcnow
from core.redact import redact


class BudgetExceededError(RuntimeError):
    """A run tried to make more model calls than it is allowed."""


STEP_PREFIX = "step:"
WORKFLOW_KIND = "workflow"


@dataclass
class GraphContext:
    toolkit: MCPToolkit
    llm: StructuredLLM
    session_factory: sessionmaker[Session]
    config: GraphConfig = field(default_factory=GraphConfig)
    registry: PromptRegistry | None = None
    _known_cards: dict[int, bool] = field(default_factory=dict, init=False, repr=False)

    def _card_fk(self, session: Session, card_id: int) -> int | None:
        """The card id if the card exists, else None. A run on a missing card must still be recorded
        (it is how "card not found" is reported), and the AIRun foreign key would reject the id."""
        if card_id not in self._known_cards:
            self._known_cards[card_id] = session.get(Card, card_id) is not None
        return card_id if self._known_cards[card_id] else None

    # ---- data and models ------------------------------------------------------------------
    async def tool(self, name: str, **arguments: Any) -> Any:
        """Call an MCP tool; a tool error raises ToolException, a hung tool raises TimeoutError."""
        try:
            return await asyncio.wait_for(self.toolkit.call(name, **arguments), self.config.tool_timeout)
        except TimeoutError as exc:
            raise TimeoutError(f"MCP tool '{name}' did not answer within {self.config.tool_timeout:g}s") from exc

    # ---- limits ---------------------------------------------------------------------------
    def clip(self, text: str) -> str:
        """Cut card text to the configured length before it goes to a model (cost and context limit)."""
        limit = self.config.max_text_chars
        return text if len(text) <= limit else text[:limit].rstrip() + " [truncated]"

    def quotes(self, context: dict[str, Any]) -> list[str]:
        """Feedback quotes for a prompt: at most max_feedback_quotes, each at most 500 characters."""
        quotes = [f["quote"] for f in context["feedback"]][: self.config.max_feedback_quotes]
        return [q if len(q) <= 500 else q[:500].rstrip() + " [truncated]" for q in quotes]

    def check_budget(self, step_id: int) -> None:
        """Raise if the run this step belongs to has used its model-call budget."""
        with session_scope(self.session_factory) as session:
            run_id = session.scalar(select(AIRun.parent_run_id).where(AIRun.id == step_id))
            if run_id is None:
                return
            calls = (
                session.scalar(
                    select(func.count(AIRun.id))
                    .join(step_alias := aliased(AIRun), AIRun.parent_run_id == step_alias.id)
                    .where(step_alias.parent_run_id == run_id, AIRun.prompt_name.is_not(None))
                )
                or 0
            )
        if calls >= self.config.max_llm_calls:
            raise BudgetExceededError(
                f"This run has made {calls} model calls, its limit (max_llm_calls={self.config.max_llm_calls})"
            )

    async def prompt(self, name: str, variables: dict[str, Any], *, card_id: int, parent_run_id: int) -> RunResult:
        """Run a prompt and log it under the step. Runs in a thread so the event loop (which also
        serves the MCP connections) is not blocked for the length of a slow model call."""
        await asyncio.to_thread(self.check_budget, parent_run_id)
        return await asyncio.to_thread(
            run_prompt_logged,
            name,
            variables,
            session_factory=self.session_factory,
            card_id=card_id,
            parent_run_id=parent_run_id,
            llm=self.llm,
            registry=self.registry,
        )

    # ---- AIRun bookkeeping ----------------------------------------------------------------
    def begin_step(self, run_id: int, card_id: int, step: str, key: str | None = None) -> int:
        """Create the step's AIRun row, or reuse the unfinished one.

        Reuse matters because LangGraph re-runs an interrupted node from the top when it resumes;
        the row must not be created twice.
        """
        kind = STEP_PREFIX + step
        with session_scope(self.session_factory) as session:
            rows = session.scalars(
                select(AIRun).where(
                    AIRun.parent_run_id == run_id,
                    AIRun.kind == kind,
                    AIRun.status.in_([RunStatus.running, RunStatus.awaiting_human]),
                )
            )
            for row in rows:
                if (row.input or {}).get("key") == key:
                    row.status = RunStatus.running
                    return row.id
            row = AIRun(
                kind=kind,
                parent_run_id=run_id,
                card_id=self._card_fk(session, card_id),
                status=RunStatus.running,
                input={"key": key} if key else None,
            )
            session.add(row)
            session.flush()
            return row.id

    def finish_step(
        self,
        step_id: int,
        status: RunStatus,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with session_scope(self.session_factory) as session:
            row = session.get(AIRun, step_id)
            if row is None:
                return
            now = utcnow()
            row.status, row.output, row.error, row.finished_at = status, output, redact(error), now
            row.confidence = (output or {}).get("confidence") if isinstance(output, dict) else None
            row.latency_ms = round((now - row.created_at).total_seconds() * 1000)

    def set_output(self, step_id: int, output: dict[str, Any]) -> None:
        """Store interim output on a step row (the question a paused checkpoint is waiting on)."""
        with session_scope(self.session_factory) as session:
            row = session.get(AIRun, step_id)
            if row is not None:
                row.output = output

    def record_skipped(self, run_id: int, card_id: int, step: str, reason: str) -> None:
        with session_scope(self.session_factory) as session:
            now = utcnow()
            session.add(
                AIRun(
                    kind=STEP_PREFIX + step,
                    parent_run_id=run_id,
                    card_id=self._card_fk(session, card_id),
                    status=RunStatus.skipped,
                    output={"reason": reason},
                    finished_at=now,
                    latency_ms=0,
                )
            )

    def set_status(self, run_id: int, status: RunStatus) -> None:
        with session_scope(self.session_factory) as session:
            row = session.get(AIRun, run_id)
            if row is not None:
                row.status = status

    def previous_result(self, card_id: int, step: str, exclude_run_id: int) -> dict[str, Any] | None:
        """The card's most recent successful result of `step` from an earlier run, if any."""
        with session_scope(self.session_factory) as session:
            row = session.scalar(
                select(AIRun)
                .where(
                    AIRun.card_id == card_id,
                    AIRun.kind == STEP_PREFIX + step,
                    AIRun.status == RunStatus.succeeded,
                    AIRun.parent_run_id != exclude_run_id,
                    AIRun.output.is_not(None),
                )
                .order_by(AIRun.id.desc())
                .limit(1)
            )
            return dict(row.output) if row is not None and row.output else None

    def other_active_run(self, card_id: int, run_id: int) -> int | None:
        with session_scope(self.session_factory) as session:
            return session.scalar(
                select(AIRun.id)
                .where(
                    AIRun.card_id == card_id,
                    AIRun.kind == WORKFLOW_KIND,
                    AIRun.id != run_id,
                    AIRun.status.in_([RunStatus.running, RunStatus.awaiting_human]),
                )
                .order_by(AIRun.id.desc())
                .limit(1)
            )
