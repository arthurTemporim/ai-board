"""The workflow runtime owned by the API process.

One `Workflow` (and with it one set of MCP server processes and one checkpoint connection) is started
on the first request and shared by every run: runs are background asyncio tasks, so a request returns
as soon as the run is recorded. Reading a run's status never touches this object; it comes from the
AIRun rows (see ai.graph.reports), so it keeps working while a run is busy.

Limits (environment): WORKFLOW_MAX_CONCURRENT_RUNS (default 4) active runs at once;
one running run per card at a time; each run is also bounded by GraphConfig.run_timeout.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from ai.graph.nodes import parse_decision
from ai.graph.reports import describe_run
from ai.graph.state import GraphConfig, normalise_steps
from ai.graph.workflow import Workflow
from ai.llm.factory import StructuredLLM
from core.db import make_session_factory, session_scope
from core.errors import ConflictError, InvalidInputError, RateLimitError
from core.models import AIRun, RunStatus
from core.models.types import utcnow
from core.redact import redacted_traceback

log = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = 4


class WorkflowRuntime:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] | None = None,
        env: dict[str, str] | None = None,
        llm: StructuredLLM | None = None,
        config: GraphConfig | None = None,
        checkpoint_path: str | os.PathLike[str] | None = None,
        max_concurrent: int | None = None,
    ):
        self._session_factory = session_factory
        self._env, self._llm, self._config, self._checkpoint_path = env, llm, config, checkpoint_path
        self.max_concurrent = max_concurrent or int(
            os.environ.get("WORKFLOW_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT)
        )
        self._workflow: Workflow | None = None
        self._stop = asyncio.Event()
        self._host: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._tasks: dict[int, asyncio.Task[Any]] = {}
        self._active_cards: set[int] = set()

    @property
    def session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            self._session_factory = make_session_factory()
        return self._session_factory

    # ---- lifecycle ----------------------------------------------------------------------
    async def ensure_started(self) -> Workflow:
        async with self._start_lock:
            if self._workflow is None:
                loop = asyncio.get_running_loop()
                ready: asyncio.Future[Workflow] = loop.create_future()
                self._stop = asyncio.Event()
                # The MCP sessions use anyio scopes that must be entered and exited by the same task, but
                # every HTTP request runs in its own task. So one dedicated task owns the workflow for
                # the life of the process and the requests only use it.
                self._host = loop.create_task(self._host_workflow(ready, self._stop))
                self._workflow = await ready
                self._fail_interrupted_runs()
            return self._workflow

    async def _host_workflow(self, ready: asyncio.Future[Workflow], stop: asyncio.Event) -> None:
        try:
            workflow = Workflow(
                session_factory=self.session_factory,
                env=self._env,
                llm=self._llm,
                config=self._config,
                checkpoint_path=self._checkpoint_path,
            )
            async with workflow:
                ready.set_result(workflow)
                await stop.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:
                raise

    def _fail_interrupted_runs(self) -> None:
        """Runs left "running" by a previous process will never finish: mark them failed.
        Runs waiting for a person are kept: their state is in the checkpoint file."""
        with session_scope(self.session_factory) as session:
            stale = set(
                session.scalars(select(AIRun.id).where(AIRun.kind == "workflow", AIRun.status == RunStatus.running))
            )
            if not stale:
                return
            ids = set(stale)
            for _ in range(2):  # steps, then the model calls under them
                ids |= set(session.scalars(select(AIRun.id).where(AIRun.parent_run_id.in_(ids))))
            session.execute(
                update(AIRun)
                .where(AIRun.id.in_(ids), AIRun.status == RunStatus.running)
                .values(status=RunStatus.failed, error="Interrupted by a server restart", finished_at=utcnow())
            )

    async def close(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        if self._workflow is not None:
            self._stop.set()
            await self._host
            self._workflow = None

    # ---- submitting ---------------------------------------------------------------------
    def _check_capacity(self, card_id: int) -> None:
        if len(self._tasks) >= self.max_concurrent:
            raise RateLimitError(
                f"{len(self._tasks)} runs are already active (limit {self.max_concurrent}); try again shortly"
            )
        if card_id in self._active_cards:
            raise ConflictError(f"A run is already in progress for card {card_id}", code="run_in_progress")

    async def submit(self, card_id: int, steps: list[str] | None, created_by_id: int | None) -> int:
        """Record a run and start it in the background; returns its id."""
        workflow = await self.ensure_started()
        self._check_capacity(card_id)
        self._active_cards.add(card_id)
        try:
            run_id, state = await workflow.begin(card_id, steps, created_by_id=created_by_id)
        except BaseException:
            self._active_cards.discard(card_id)
            raise
        self._spawn(run_id, card_id, workflow.execute(run_id, state))
        return run_id

    async def resume(self, run_id: int, decision: dict[str, Any]) -> None:
        """Validate a person's answer to a paused run and continue it in the background."""
        workflow = await self.ensure_started()
        with session_scope(self.session_factory) as session:
            report = describe_run(session, run_id)
        question = report["pending_action"]
        if report["status"] != "awaiting_human" or question is None:
            raise ConflictError(f"Run {run_id} is not waiting for an answer", code="not_waiting")
        try:
            parse_decision(question, decision)
        except ValueError as exc:
            raise InvalidInputError(f"Invalid answer: {exc}", code="invalid_decision") from exc
        card_id = int(report["card_id"] or 0)
        if run_id in self._tasks:
            raise ConflictError(f"Run {run_id} is already being resumed", code="run_in_progress")
        self._check_capacity(card_id)
        self._active_cards.add(card_id)
        self._spawn(run_id, card_id, workflow.resume(run_id, decision))

    def _spawn(self, run_id: int, card_id: int, coroutine: Any) -> None:
        async def runner() -> None:
            try:
                await coroutine
            except asyncio.CancelledError:
                raise
            except Exception:  # already recorded on the run; keep the server's log quiet but useful
                log.error("workflow run %s failed\n%s", run_id, redacted_traceback())
            finally:
                self._tasks.pop(run_id, None)
                self._active_cards.discard(card_id)

        self._tasks[run_id] = asyncio.get_running_loop().create_task(runner())

    @property
    def active_runs(self) -> int:
        return len(self._tasks)

    @staticmethod
    def normalise(steps: list[str] | str | None) -> list[str] | None:
        try:
            return normalise_steps(steps)
        except ValueError as exc:
            raise InvalidInputError(str(exc), code="unknown_step") from exc
