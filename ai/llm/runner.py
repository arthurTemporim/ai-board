"""run_prompt(): render, call the model, and log the run in AIRun.

The session is only flushed, never committed: the caller owns the transaction. If the model call
fails the AIRun row is marked failed and the error is re-raised; callers that want to keep the
failed row must catch the error and commit instead of rolling back.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ai.llm.factory import StructuredLLM, get_llm
from ai.llm.registry import PromptRegistry, default_registry
from core.models import AIRun, RunStatus
from core.models.types import utcnow


@dataclass
class RunResult:
    output: BaseModel
    prompt_ref: str
    model: str
    input_hash: str
    latency_ms: int
    run_id: int | None = None


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
            run.error = f"{type(exc).__name__}: {exc}"[:2000]
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
    )
