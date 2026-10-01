"""Run the graph: start, pause at a human checkpoint, resume (even from another process), report.

    async with Workflow() as workflow:
        report = await workflow.start(card_id=3, steps="analyze,score")
        if report.interrupt:                       # waiting for a person
            report = await workflow.resume(report.run_id, {"actionable": True})

The run id is the workflow's AIRun row. The LangGraph thread id is a separate random id stored on
that row: database ids can be reused (for example after the seed script wipes the database) while
old checkpoints survive in their own file, and reusing a thread id would resume somebody else's state.
State is checkpointed to SQLite after every node, so a paused run survives the process that started it.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai.graph.build import build_graph
from ai.graph.context import WORKFLOW_KIND, GraphContext
from ai.graph.state import GraphConfig, normalise_steps
from ai.llm.factory import StructuredLLM, get_llm
from ai.llm.registry import PromptRegistry
from ai.mcp_servers.client import MCPToolkit
from core.db import make_session_factory, session_scope
from core.models import AIRun, Draft, RunStatus

DEFAULT_CHECKPOINT_PATH = ".data/checkpoints.db"


@dataclass
class RunReport:
    run_id: int
    card_id: int
    status: str  # running | awaiting_human | succeeded | failed
    outcome: str | None = None  # completed | needs_info | duplicate_proposed | failed
    steps: dict[str, str] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    human_decisions: list[dict[str, Any]] = field(default_factory=list)
    interrupt: dict[str, Any] | None = None  # the question a person has to answer, if paused
    suggestions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Workflow:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | None = None,
        env: dict[str, str] | None = None,
        llm: StructuredLLM | None = None,
        config: GraphConfig | None = None,
        checkpoint_path: str | os.PathLike[str] | None = None,
        registry: PromptRegistry | None = None,
        servers: list[str] | None = None,
    ):
        self.session_factory = session_factory or make_session_factory()
        self._env, self._servers = env, servers
        self._llm, self._registry = llm, registry
        self.config = config or GraphConfig.from_env()
        self._checkpoint_path = Path(
            checkpoint_path or os.environ.get("GRAPH_CHECKPOINT_PATH") or DEFAULT_CHECKPOINT_PATH
        )
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> Workflow:
        toolkit = await self._stack.enter_async_context(MCPToolkit(self._servers, self._env))
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpointer = await self._stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(self._checkpoint_path))
        )
        self.ctx = GraphContext(toolkit, self._llm or get_llm(), self.session_factory, self.config, self._registry)
        self.graph = build_graph(self.ctx).compile(checkpointer=checkpointer)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._stack.aclose()

    # ---- running ------------------------------------------------------------------------
    async def start(
        self, card_id: int, steps: list[str] | str | None = None, *, created_by_id: int | None = None
    ) -> RunReport:
        """Start a run on a card and wait for it to finish or pause. `steps` picks which steps to run."""
        run_id, state = await self.begin(card_id, steps, created_by_id=created_by_id)
        return await self._drive(run_id, state)

    async def begin(
        self, card_id: int, steps: list[str] | str | None = None, *, created_by_id: int | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Record a new run and return (run_id, initial state) without running it; see `execute`."""
        requested = normalise_steps(steps)
        with session_scope(self.session_factory) as session:
            run = AIRun(
                kind=WORKFLOW_KIND,
                card_id=self.ctx._card_fk(session, card_id),
                status=RunStatus.running,
                created_by_id=created_by_id,
                input={
                    "card_id": card_id,
                    "steps": requested,
                    "config": asdict(self.config),
                    "thread_id": uuid.uuid4().hex,
                },
            )
            session.add(run)
            session.flush()
            run_id = run.id
        state = {
            "card_id": card_id,
            "run_id": run_id,
            "steps_requested": requested,
            "results": {},
            "statuses": {},
            "errors": [],
            "human_decisions": [],
        }
        return run_id, state

    async def execute(self, run_id: int, state: dict[str, Any]) -> RunReport:
        """Run a recorded run to completion or until it pauses for a person."""
        return await self._drive(run_id, state)

    async def resume(self, run_id: int, decision: dict[str, Any]) -> RunReport:
        """Answer the question a paused run is waiting on and let it continue."""
        if (await self.report(run_id)).interrupt is None:
            raise ValueError(f"Run {run_id} is not waiting for an answer")
        return await self._drive(run_id, Command(resume=decision))

    async def _drive(self, run_id: int, graph_input: Any) -> RunReport:
        thread = self._thread(run_id)
        try:
            await asyncio.wait_for(self.graph.ainvoke(graph_input, thread), self.config.run_timeout)
        except TimeoutError:
            self.ctx.finish_step(
                run_id, RunStatus.failed, error=f"The run took longer than {self.config.run_timeout:g}s"
            )
            raise
        except asyncio.CancelledError:
            self.ctx.finish_step(run_id, RunStatus.failed, error="The run was cancelled (server shutting down)")
            raise
        except Exception as exc:  # a bug in the graph itself; node errors never get here
            self.ctx.finish_step(run_id, RunStatus.failed, error=f"{type(exc).__name__}: {exc}"[:2000])
            raise
        return await self.report(run_id)

    def _thread(self, run_id: int) -> dict[str, Any]:
        with session_scope(self.session_factory) as session:
            row = session.get(AIRun, run_id)
            thread_id = (row.input or {}).get("thread_id") if row is not None and row.kind == WORKFLOW_KIND else None
        if thread_id is None:
            raise ValueError(f"No run {run_id}")
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}

    # ---- reporting ----------------------------------------------------------------------
    async def report(self, run_id: int) -> RunReport:
        snapshot = await self.graph.aget_state(self._thread(run_id))
        values = snapshot.values or {}
        if not values:
            raise ValueError(f"No run {run_id}")
        questions = [i.value for task in snapshot.tasks for i in task.interrupts]
        with session_scope(self.session_factory) as session:
            row = session.get(AIRun, run_id)
            status = str(row.status) if row is not None else "unknown"
        return RunReport(
            run_id=run_id,
            card_id=values["card_id"],
            status=status,
            outcome=values.get("outcome"),
            steps=dict(values.get("statuses") or {}),
            results=dict(values.get("results") or {}),
            errors=list(values.get("errors") or []),
            human_decisions=list(values.get("human_decisions") or []),
            interrupt=questions[0] if questions else None,
            suggestions=self.suggestions(run_id),
        )

    def suggestions(self, run_id: int) -> list[dict[str, Any]]:
        """Drafts created during this run (by the run, its steps, or their model calls)."""
        with session_scope(self.session_factory) as session:
            ids, frontier = {run_id}, {run_id}
            for _ in range(3):  # workflow -> step -> model call
                frontier = set(session.scalars(select(AIRun.id).where(AIRun.parent_run_id.in_(frontier)))) - ids
                ids |= frontier
            drafts = session.scalars(select(Draft).where(Draft.run_id.in_(ids)).order_by(Draft.id))
            return [
                {
                    "id": d.id,
                    "kind": str(d.kind),
                    "status": str(d.status),
                    "confidence": d.confidence,
                    "payload": d.payload,
                }
                for d in drafts
            ]
