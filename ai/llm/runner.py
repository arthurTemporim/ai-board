"""run_prompt(): render, call the model, and log the run in AIRun.

run_prompt logs through the caller's session, which it only flushes: the caller owns the
transaction. If the model call fails the AIRun row is marked failed and the error is re-raised;
callers that want to keep the failed row must catch it and commit instead of rolling back.

run_prompt_logged does the same with its own short transactions, which it commits. A local model
can take half a minute, and holding a write transaction open that long would lock out every other
process writing to the SQLite database (the MCP servers), so the graph uses this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from ai.llm.factory import StructuredLLM, get_llm
from ai.llm.registry import PromptRegistry, default_registry
from core.db import session_scope
from core.models import AIRun, RunStatus
from core.models.types import utcnow
from core.redact import redact


@dataclass
class RunResult:
    output: BaseModel
    prompt_ref: str
    model: str
    input_hash: str
    latency_ms: int
    run_id: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


def run_prompt(
    name: str,
    variables: dict[str, Any],
    *,
    session: Session | None = None,
    card_id: int | None = None,
    parent_run_id: int | None = None,
    created_by_id: int | None = None,
    version: int | None = None,
    llm: StructuredLLM | None = None,
    registry: PromptRegistry | None = None,
) -> RunResult:
    rendered = (registry or default_registry()).render(name, variables, version)
    llm = llm or get_llm()
    model = llm.model_for(rendered)

    run: AIRun | None = None
    if session is not None:
        run = AIRun(
            kind=name,
            status=RunStatus.running,
            parent_run_id=parent_run_id,
            card_id=card_id,
            created_by_id=created_by_id,
            prompt_name=rendered.name,
            prompt_version=rendered.version,
            model=model,
            input_hash=rendered.input_hash,
            input={"variables": variables},
        )
        session.add(run)
        session.flush()

    started = perf_counter()
    try:
        result = llm.invoke(rendered)
    except Exception as exc:
        if run is not None and session is not None:
            run.status = RunStatus.failed
            run.error = (redact(f"{type(exc).__name__}: {exc}") or "")[:2000]
            run.latency_ms = round((perf_counter() - started) * 1000)
            run.finished_at = utcnow()
            session.flush()
        raise

    latency_ms = round((perf_counter() - started) * 1000)
    if run is not None and session is not None:
        run.status = RunStatus.succeeded
        run.output = result.output.model_dump(mode="json")
        run.confidence = getattr(result.output, "confidence", None)
        run.latency_ms = latency_ms
        run.tokens_in, run.tokens_out = result.tokens_in, result.tokens_out
        run.finished_at = utcnow()
        session.flush()

    return RunResult(
        output=result.output,
        prompt_ref=rendered.ref,
        model=model,
        input_hash=rendered.input_hash,
        latency_ms=latency_ms,
        run_id=run.id if run is not None else None,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )


def run_prompt_logged(
    name: str,
    variables: dict[str, Any],
    *,
    session_factory: sessionmaker[Session],
    card_id: int | None = None,
    parent_run_id: int | None = None,
    created_by_id: int | None = None,
    version: int | None = None,
    llm: StructuredLLM | None = None,
    registry: PromptRegistry | None = None,
) -> RunResult:
    """run_prompt with its own short transactions: the row is committed as "running" before the
    model is called and updated afterwards, and no database transaction is open during the call."""
    rendered = (registry or default_registry()).render(name, variables, version)
    llm = llm or get_llm()
    model = llm.model_for(rendered)

    with session_scope(session_factory) as session:
        run = AIRun(
            kind=name,
            status=RunStatus.running,
            parent_run_id=parent_run_id,
            card_id=card_id,
            created_by_id=created_by_id,
            prompt_name=rendered.name,
            prompt_version=rendered.version,
            model=model,
            input_hash=rendered.input_hash,
            input={"variables": variables},
        )
        session.add(run)
        session.flush()
        run_id = run.id

    started = perf_counter()
    try:
        result = llm.invoke(rendered)
    except Exception as exc:
        with session_scope(session_factory) as session:
            failed = session.get(AIRun, run_id)
            if failed is not None:
                failed.status = RunStatus.failed
                failed.error = (redact(f"{type(exc).__name__}: {exc}") or "")[:2000]
                failed.latency_ms = round((perf_counter() - started) * 1000)
                failed.finished_at = utcnow()
        raise

    latency_ms = round((perf_counter() - started) * 1000)
    with session_scope(session_factory) as session:
        done = session.get(AIRun, run_id)
        if done is not None:
            done.status = RunStatus.succeeded
            done.output = result.output.model_dump(mode="json")
            done.confidence = getattr(result.output, "confidence", None)
            done.latency_ms = latency_ms
            done.tokens_in, done.tokens_out = result.tokens_in, result.tokens_out
            done.finished_at = utcnow()
    return RunResult(
        output=result.output,
        prompt_ref=rendered.ref,
        model=model,
        input_hash=rendered.input_hash,
        latency_ms=latency_ms,
        run_id=run_id,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )
