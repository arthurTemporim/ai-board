"""Start, watch and resume AI triage runs. The graph itself runs in the background; see apps/api/workflows.py."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select

from ai.graph.context import WORKFLOW_KIND
from ai.graph.reports import describe_run
from apps.api.deps import OptionalActorDep, SessionDep
from apps.api.schemas import (
    ResumeRequest,
    RunAccepted,
    RunRead,
    RunSummary,
    StepRequest,
    TriageCard,
    TriageRequest,
)
from apps.api.workflows import WorkflowRuntime
from core.db import session_scope
from core.errors import InvalidInputError, NotFoundError
from core.models import AIRun
from core.services.cards import CardService

router = APIRouter(prefix="/workflows", tags=["workflows"])


def get_runtime(request: Request) -> WorkflowRuntime:
    return request.app.state.workflow_runtime


RuntimeDep = Annotated[WorkflowRuntime, Depends(get_runtime)]


def _create_card(runtime: WorkflowRuntime, payload: TriageCard, actor_id: int | None) -> int:
    """Create the card in its own committed transaction: the graph reads it from other processes."""
    with session_scope(runtime.session_factory) as session:
        card = CardService(session).create_card(created_by_id=actor_id, **payload.model_dump())
        return card.id


async def _submit(
    runtime: WorkflowRuntime, card_id: int | None, card: TriageCard | None, steps, actor_id
) -> RunAccepted:
    requested = runtime.normalise(steps)
    created = card is not None
    if card is not None:
        card_id = _create_card(runtime, card, actor_id)
    else:
        with session_scope(runtime.session_factory) as session:
            CardService(session).get_card(card_id)  # 404 before anything is recorded
    assert card_id is not None
    run_id = await runtime.submit(card_id, requested, actor_id)
    return RunAccepted(
        run_id=run_id, card_id=card_id, status="running", steps_requested=requested, card_created=created
    )


@router.post(
    "/triage",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run AI triage on a card (existing, or created from a payload)",
)
async def triage(body: TriageRequest, runtime: RuntimeDep, actor_id: OptionalActorDep):
    return await _submit(runtime, body.card_id, body.card, body.steps, actor_id)


@router.post(
    "/steps/{step}",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run a single triage step",
)
async def run_step(step: str, body: StepRequest, runtime: RuntimeDep, actor_id: OptionalActorDep):
    requested = runtime.normalise([step])
    if requested is None:  # "all" is not a single step
        raise InvalidInputError("Name one step", code="unknown_step")
    return await _submit(runtime, body.card_id, body.card, requested, actor_id)


@router.get("/runs", response_model=list[RunSummary], summary="Recent runs, newest first")
def list_runs(
    session: SessionDep,
    card_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    stmt = select(AIRun).where(AIRun.kind == WORKFLOW_KIND).order_by(AIRun.id.desc()).limit(limit)
    if card_id is not None:
        stmt = stmt.where(AIRun.card_id == card_id)
    return [
        RunSummary(
            run_id=r.id,
            card_id=r.card_id,
            status=str(r.status),
            outcome=(r.output or {}).get("outcome") if isinstance(r.output, dict) else None,
            created_at=r.created_at,
            finished_at=r.finished_at,
        )
        for r in session.scalars(stmt)
    ]


@router.get(
    "/runs/{run_id}",
    response_model=RunRead,
    summary="Status of a run: per-step results, progress and the pending human action",
)
def get_run(run_id: int, session: SessionDep):
    return describe_run(session, run_id)


@router.post(
    "/runs/{run_id}/resume",
    response_model=RunSummary,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Answer the question a paused run is waiting on",
)
async def resume_run(run_id: int, body: ResumeRequest, runtime: RuntimeDep):
    with session_scope(runtime.session_factory) as session:
        run = session.get(AIRun, run_id)
        if run is None or run.kind != WORKFLOW_KIND:
            raise NotFoundError(f"Run {run_id} not found")
        card_id, created = run.card_id, run.created_at
    await runtime.resume(run_id, body.decision)
    return RunSummary(
        run_id=run_id, card_id=card_id, status="running", outcome=None, created_at=created, finished_at=None
    )
