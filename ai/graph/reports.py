"""Describe a workflow run from the database alone (AIRun rows), without the graph or the MCP servers.

This is what the API serves while a run is going or after the server restarted: every step writes
its status and result to its own AIRun row, and a paused checkpoint stores its question there.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai.graph.context import STEP_PREFIX, WORKFLOW_KIND
from ai.graph.state import STEPS
from core.errors import NotFoundError
from core.models import AIRun, RunStatus
from core.redact import redact

ROUTES = ("human_checkpoint", "needs_info", "merge_proposal")
FINISHED = (RunStatus.succeeded, RunStatus.failed, RunStatus.skipped)


def _step(row: AIRun) -> dict[str, Any]:
    out = row.output if isinstance(row.output, dict) else {}
    step: dict[str, Any] = {
        "name": row.kind.removeprefix(STEP_PREFIX),
        "status": str(row.status),
        "requested": True,
        "started_at": row.created_at,
        "finished_at": row.finished_at,
        "latency_ms": row.latency_ms,
        "result": None,
        "error": redact(row.error),
        "reason": None,
    }
    if row.status == RunStatus.skipped:
        step["reason"] = out.get("reason")
    elif row.status == RunStatus.succeeded:
        step["result"] = out
    return step


def describe_run(session: Session, run_id: int) -> dict[str, Any]:
    run = session.get(AIRun, run_id)
    if run is None or run.kind != WORKFLOW_KIND:
        raise NotFoundError(f"Run {run_id} not found")
    requested: list[str] | None = (run.input or {}).get("steps")
    rows = list(
        session.scalars(
            select(AIRun).where(AIRun.parent_run_id == run_id, AIRun.kind.like(STEP_PREFIX + "%")).order_by(AIRun.id)
        )
    )
    last: dict[str, AIRun] = {}
    for row in rows:
        last[row.kind.removeprefix(STEP_PREFIX)] = row

    steps: list[dict[str, Any]] = []
    for name in STEPS:
        is_requested = requested is None or name in requested
        if name in last:
            step = _step(last[name])
            step["requested"] = is_requested
        else:
            step = {
                "name": name,
                "status": "pending" if is_requested else "not_requested",
                "requested": is_requested,
                "started_at": None,
                "finished_at": None,
                "latency_ms": None,
                "result": None,
                "error": None,
                "reason": None,
            }
        steps.append(step)
    for name in ROUTES:
        if name in last:
            steps.append({**_step(last[name]), "requested": False, "route": True})

    pending: dict[str, Any] | None = None
    decisions: list[dict[str, Any]] = []
    for row in rows:
        if row.kind != STEP_PREFIX + "human_checkpoint":
            continue
        out = row.output if isinstance(row.output, dict) else {}
        if row.status == RunStatus.awaiting_human and "question" in out:
            pending = out["question"]
        elif row.status == RunStatus.succeeded and "decision" in out:
            decisions.append({"checkpoint": out.get("kind"), "decision": out["decision"]})

    wanted = [s for s in steps if s["requested"] and not s.get("route")]
    done = [s for s in wanted if s["status"] in {str(x) for x in FINISHED}]
    finished = run.status in (RunStatus.succeeded, RunStatus.failed)
    output = run.output if isinstance(run.output, dict) else {}
    errors = [{"step": s["name"], "error": s["error"]} for s in steps if s["status"] == "failed" and s["error"]]
    if run.status == RunStatus.failed and run.error and not errors:
        errors = [{"step": "run", "error": redact(run.error)}]
    return {
        "run_id": run.id,
        "card_id": run.card_id if run.card_id is not None else (run.input or {}).get("card_id"),
        "status": str(run.status),
        "outcome": output.get("outcome"),
        "steps": steps,
        "progress": {"done": len(wanted) if finished else len(done), "total": len(wanted)},
        "pending_action": pending,
        "human_decisions": decisions,
        "errors": errors,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }
