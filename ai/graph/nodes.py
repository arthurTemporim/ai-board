"""The graph's nodes. `make_node` wraps each body with the rules every step shares:

1. Skipped unless its step was requested (recorded as a skipped AIRun, not silently ignored).
2. Dependencies come from this run, else from the card's latest earlier run; if neither exists the
   step is skipped with the reason, instead of running on missing input.
3. Its status, result and any error are written to its own AIRun row.
4. An exception is recorded and the graph carries on: later steps that do not need this one still run.

Bodies fetch data through MCP tools, call the LLM through the prompt registry, and store what the
model produced as suggestions (MCP tools that only propose). Nothing here changes a card.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import ToolException
from langgraph.errors import GraphBubbleUp
from langgraph.types import interrupt

from ai.graph.context import GraphContext
from ai.graph.state import DEPENDENCIES, OPTIONAL_DEPENDENCIES, STEPS, WorkflowState
from core.models import RunStatus
from core.redact import redact

VERDICTS = ("duplicate", "related", "distinct")


class StopRun(Exception):
    """Something makes the rest of the run pointless (for example the card does not exist)."""


@dataclass
class StepOutcome:
    result: dict[str, Any]
    update: dict[str, Any] = field(default_factory=dict)  # extra state updates: route, merge_target, ...


Body = Callable[[GraphContext, WorkflowState, dict[str, Any], int], Awaitable[StepOutcome]]


def is_requested(state: WorkflowState, step: str) -> bool:
    steps = state.get("steps_requested")
    return steps is None or step in steps


def _message(exc: BaseException) -> str:
    return redact(f"{type(exc).__name__}: {exc}") or "error"


async def _inputs(ctx: GraphContext, state: WorkflowState, step: str) -> tuple[dict[str, Any], str | None]:
    """Results this step depends on, or the reason it cannot run."""
    results, statuses = state.get("results") or {}, state.get("statuses") or {}
    card_id, run_id = state["card_id"], state["run_id"]
    inputs: dict[str, Any] = {}
    for dep in DEPENDENCIES.get(step, ()):
        value = results.get(dep)
        if value is None:
            if statuses.get(dep) == "failed":
                return {}, f"dependency failed: {dep}"
            value = await asyncio.to_thread(ctx.previous_result, card_id, dep, run_id)
        if value is None:
            return {}, f"missing dependency: {dep} (not run now and no earlier result for this card)"
        inputs[dep] = value
    for dep in OPTIONAL_DEPENDENCIES.get(step, ()):
        value = results.get(dep)
        if value is None and statuses.get(dep) != "failed":
            value = await asyncio.to_thread(ctx.previous_result, card_id, dep, run_id)
        inputs[dep] = value
    return inputs, None


def make_node(ctx: GraphContext, step: str, body: Body, *, selectable: bool = True):
    async def node(state: WorkflowState) -> dict[str, Any]:
        run_id, card_id = state["run_id"], state["card_id"]

        def skipped(reason: str) -> dict[str, Any]:
            ctx.record_skipped(run_id, card_id, step, reason)
            return {"statuses": {step: "skipped"}, "route": "continue"}

        if selectable and not is_requested(state, step):
            return skipped("not requested")
        inputs, missing = await _inputs(ctx, state, step)
        if missing:
            return skipped(missing)

        key = (state.get("checkpoint_request") or {}).get("kind") if step == "human_checkpoint" else None
        step_id = ctx.begin_step(run_id, card_id, step, key)
        try:
            outcome = await body(ctx, state, inputs, step_id)
        except GraphBubbleUp:
            raise  # an interrupt is not a failure: the step stays "awaiting_human" until resumed
        except StopRun as exc:
            ctx.finish_step(step_id, RunStatus.failed, error=str(exc))
            return {
                "statuses": {step: "failed"},
                "errors": [{"step": step, "error": str(exc)}],
                "route": "stop",
                "outcome": "failed",
            }
        except Exception as exc:
            message = redact(_message(exc)) or "error"
            ctx.finish_step(step_id, RunStatus.failed, error=message)
            return {
                "statuses": {step: "failed"},
                "errors": [{"step": step, "error": message}],
                "route": "continue",
            }
        ctx.finish_step(step_id, RunStatus.succeeded, output=outcome.result)
        return {
            "results": {step: outcome.result},
            "statuses": {step: "succeeded"},
            "route": "continue",
            **outcome.update,
        }

    node.__name__ = step
    return node


# ---- helpers ----------------------------------------------------------------------------------
async def _context(ctx: GraphContext, state: WorkflowState) -> dict[str, Any]:
    return await ctx.tool("get_card_context", card_id=state["card_id"])


def _quotes(ctx: GraphContext, context: dict[str, Any]) -> list[str]:
    return ctx.quotes(context)


def _dump(output: Any) -> dict[str, Any]:
    return output.model_dump(mode="json")


# ---- validate_intake --------------------------------------------------------------------------
async def validate_intake(ctx: GraphContext, state: WorkflowState, _inputs: dict, _step_id: int) -> StepOutcome:
    card_id = state["card_id"]
    try:
        context = await _context(ctx, state)
    except ToolException as exc:
        if "not found" in str(exc):
            raise StopRun(f"Card {card_id} not found") from exc
        raise
    card, warnings = context["card"], []
    if len(card["description"].strip()) < 20:
        warnings.append("the description is very short")
    if card["column"] in ("Shipped", "Declined"):
        warnings.append(f"the card is already in '{card['column']}'")
    if context["pending_suggestions"]:
        warnings.append(f"{context['pending_suggestions']} earlier suggestion(s) are still waiting for review")
    other = await asyncio.to_thread(ctx.other_active_run, card_id, state["run_id"])
    if other is not None:
        warnings.append(f"run {other} for this card has not finished")
    result = {"valid": True, "warnings": warnings, "title": card["title"], "column": card["column"]}
    return StepOutcome(result, {"board_id": card["board_id"]})


# ---- analyze_need -----------------------------------------------------------------------------
async def analyze_need(ctx: GraphContext, state: WorkflowState, _inputs: dict, step_id: int) -> StepOutcome:
    context = await _context(ctx, state)
    card = context["card"]
    run = await ctx.prompt(
        "analyze_need",
        {
            "requester_type": card["requester_type"],
            "card_title": card["title"],
            "card_description": ctx.clip(card["description"]),
            "feedback_quotes": _quotes(ctx, context),
        },
        card_id=state["card_id"],
        parent_run_id=step_id,
    )
    out = run.output
    analysis = _dump(out)
    saved = await ctx.tool(
        "save_suggestion",
        card_id=state["card_id"],
        kind="analysis",
        payload=analysis,
        confidence=out.confidence,
        run_id=run.run_id,
    )
    result = {**analysis, "llm_run_id": run.run_id, "draft_id": saved["draft_id"]}

    if not out.is_actionable:  # vague: stop triaging and ask for more detail
        return StepOutcome(result, {"route": "needs_info"})
    if out.confidence < ctx.config.analysis_min_confidence:  # actionable, but the model is unsure
        request = {
            "kind": "confirm_actionable",
            "payload": {"analysis": analysis},
            "resume_to": "find_related",
        }
        return StepOutcome(result, {"route": "checkpoint", "checkpoint_request": request, "resume_to": "find_related"})
    return StepOutcome(result)


# ---- find_related -----------------------------------------------------------------------------
def classify(verdict: str, confidence: float, link_threshold: float, merge_threshold: float) -> str:
    """What to do with one judged candidate.

    merge          a confident duplicate: create a suggested link and propose a merge
    review         a duplicate the judge is not sure enough about: a person decides
    link_related   a related card: create a suggested link
    none           distinct, or not confident enough to say anything
    """
    if confidence < link_threshold:
        return "none"
    if verdict == "duplicate":
        return "merge" if confidence >= merge_threshold else "review"
    if verdict == "related":
        return "link_related"
    return "none"


async def find_related(ctx: GraphContext, state: WorkflowState, inputs: dict, step_id: int) -> StepOutcome:
    cfg, card_id = ctx.config, state["card_id"]
    context = await _context(ctx, state)
    card = context["card"]
    await ctx.tool("index_board", board_id=card["board_id"])
    similar = await ctx.tool("find_similar_cards", card_id=card_id, top_k=cfg.top_k, min_score=cfg.min_similarity)
    decided = {link["card_id"] for link in context["links"] if link["status"] in ("confirmed", "rejected")}

    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for hit in similar["results"]:
        entry: dict[str, Any] = {"card_id": hit["card_id"], "title": hit["title"], "similarity": hit["score"]}
        candidates.append(entry)
        if hit["card_id"] in decided:  # a person already decided this pair; do not ask the model again
            entry["action"] = "skip_decided"
            continue
        try:
            other = await ctx.tool("get_card", card_id=hit["card_id"])
            judged = await ctx.prompt(
                "judge_duplicate",
                {
                    "similarity_score": hit["score"],
                    "card_a_title": card["title"],
                    "card_a_description": ctx.clip(card["description"]),
                    "card_b_title": other["title"],
                    "card_b_description": ctx.clip(other["description"]),
                },
                card_id=card_id,
                parent_run_id=step_id,
            )
            verdict = judged.output
            action = classify(verdict.verdict, verdict.confidence, cfg.link_threshold, cfg.merge_threshold)
            entry.update(
                verdict=verdict.verdict,
                confidence=verdict.confidence,
                rationale=verdict.rationale,
                evidence=verdict.evidence,
                llm_run_id=judged.run_id,
                action=action,
            )
            if action in ("merge", "link_related"):
                link = await ctx.tool(
                    "link_cards",
                    card_id=card_id,
                    other_card_id=hit["card_id"],
                    type="duplicate" if action == "merge" else "related",
                    confidence=verdict.confidence,
                    rationale=verdict.rationale,
                )
                entry["link_id"] = link["link_id"]
        except Exception as exc:  # one bad candidate must not lose the others
            entry.update(action="error", error=_message(exc))
            errors.append({"step": "find_related", "error": f"candidate {hit['card_id']}: {_message(exc)}"})

    merges = [c for c in candidates if c.get("action") == "merge"]
    reviews = [c for c in candidates if c.get("action") == "review"]
    result: dict[str, Any] = {
        "candidates": candidates,
        "links_created": sum(1 for c in candidates if "link_id" in c),
        "review": [c["card_id"] for c in reviews],
        "merge": None,
    }
    update: dict[str, Any] = {"errors": errors} if errors else {}
    if merges:
        best = sorted(merges, key=lambda c: (-c["confidence"], c["card_id"]))[0]
        target = {k: best[k] for k in ("card_id", "title", "confidence", "rationale")} | {"human_confirmed": False}
        result["merge"] = target
        update |= {"route": "merge", "merge_target": target}
    elif reviews:
        request = {
            "kind": "confirm_duplicate",
            "payload": {
                "candidates": [
                    {k: c[k] for k in ("card_id", "title", "similarity", "confidence", "rationale")} for c in reviews
                ]
            },
            "resume_to": "group_problem",
        }
        update |= {"route": "checkpoint", "checkpoint_request": request, "resume_to": "group_problem"}
    return StepOutcome(result, update)


# ---- merge_proposal (a route, not a step) -----------------------------------------------------
async def merge_proposal(ctx: GraphContext, state: WorkflowState, _inputs: dict, step_id: int) -> StepOutcome:
    target, card_id = state["merge_target"], state["card_id"]
    keep, merge = sorted([card_id, target["card_id"]])  # the older card survives
    payload = {
        "action": "merge",
        "keep_card_id": keep,
        "merge_card_id": merge,
        "source_card_id": card_id,
        "duplicate_of": target["card_id"],
        "confidence": target["confidence"],
        "rationale": target["rationale"],
        "human_confirmed": bool(target.get("human_confirmed")),
    }
    saved = await ctx.tool(
        "save_suggestion",
        card_id=card_id,
        kind="link",
        payload=payload,
        confidence=target["confidence"],
        run_id=step_id,
    )
    return StepOutcome({**payload, "draft_id": saved["draft_id"]}, {"outcome": "duplicate_proposed"})


# ---- human_checkpoint -------------------------------------------------------------------------
def parse_decision(request: dict[str, Any], value: Any) -> dict[str, Any]:
    """Validate what the person sent back; raises ValueError with a message they can act on."""
    kind = request["kind"]
    if not isinstance(value, dict):
        raise ValueError("send a JSON object")
    if kind == "confirm_actionable":
        if not isinstance(value.get("actionable"), bool):
            raise ValueError('expected {"actionable": true} or {"actionable": false}')
        return {"actionable": value["actionable"]}
    if kind == "confirm_duplicate":
        ids = [c["card_id"] for c in request["payload"]["candidates"]]
        if "verdict" in value and "decisions" not in value:  # shorthand: one verdict for every candidate
            value = {"decisions": [{"card_id": i, "verdict": value["verdict"]} for i in ids]}
        decisions = value.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError('expected {"decisions": [{"card_id": N, "verdict": "duplicate|related|distinct"}]}')
        cleaned: dict[int, str] = {}
        for item in decisions:
            if not isinstance(item, dict) or item.get("card_id") not in ids or item.get("verdict") not in VERDICTS:
                raise ValueError(f"each decision needs a card_id from {ids} and a verdict of {', '.join(VERDICTS)}")
            cleaned[item["card_id"]] = item["verdict"]
        missing = [i for i in ids if i not in cleaned]
        if missing:
            raise ValueError(f"no decision for card(s) {missing}")
        return {"decisions": [{"card_id": i, "verdict": cleaned[i]} for i in ids]}
    raise ValueError(f"unknown checkpoint kind {kind}")


async def human_checkpoint(ctx: GraphContext, state: WorkflowState, _inputs: dict, step_id: int) -> StepOutcome:
    request, run_id, card_id = state["checkpoint_request"], state["run_id"], state["card_id"]
    resume_to = request["resume_to"]

    # Everything before interrupt() must be safe to repeat: LangGraph re-runs this body on resume.
    await asyncio.to_thread(ctx.set_status, run_id, RunStatus.awaiting_human)
    await asyncio.to_thread(ctx.set_status, step_id, RunStatus.awaiting_human)
    question: dict[str, Any] = {
        "kind": request["kind"],
        "run_id": run_id,
        "card_id": card_id,
        "payload": request["payload"],
    }
    while True:
        await asyncio.to_thread(ctx.set_output, step_id, {"question": question})
        answer = interrupt(question)
        try:
            decision = parse_decision(request, answer)
            break
        except ValueError as exc:  # ask again, saying what was wrong
            question = {**question, "error": str(exc)}
    await asyncio.to_thread(ctx.set_status, run_id, RunStatus.running)

    update: dict[str, Any] = {
        "route": "continue",
        "resume_to": resume_to,
        "human_decisions": [{"checkpoint": request["kind"], "decision": decision}],
    }
    if request["kind"] == "confirm_actionable":
        if not decision["actionable"]:
            update["route"] = "needs_info"
    else:
        by_id = {c["card_id"]: c for c in request["payload"]["candidates"]}
        duplicates = []
        for item in decision["decisions"]:
            candidate = by_id[item["card_id"]]
            if item["verdict"] in ("duplicate", "related"):
                note = f" (a person judged this {item['verdict']} in run {run_id})"
                await ctx.tool(
                    "link_cards",
                    card_id=card_id,
                    other_card_id=item["card_id"],
                    type=item["verdict"],
                    confidence=candidate["confidence"],
                    rationale=candidate["rationale"][: 500 - len(note)] + note,
                )
            if item["verdict"] == "duplicate":
                duplicates.append(candidate)
        if duplicates:
            best = sorted(duplicates, key=lambda c: (-c["confidence"], c["card_id"]))[0]
            update["merge_target"] = {k: best[k] for k in ("card_id", "title", "confidence", "rationale")} | {
                "human_confirmed": True
            }
            update["route"] = "merge"
    return StepOutcome({"kind": request["kind"], "decision": decision}, update)


# ---- needs_info (a route, not a step) ---------------------------------------------------------
def _questions_text(questions: list[str]) -> str:
    return "\n".join(f"- {q}" for q in questions)


async def needs_info(ctx: GraphContext, state: WorkflowState, _inputs: dict, step_id: int) -> StepOutcome:
    card_id = state["card_id"]
    analysis = (state.get("results") or {}).get("analyze_need") or {}
    questions = analysis.get("clarifying_questions") or [
        "What problem are you trying to solve?",
        "Who is affected, and how often?",
    ]
    text = _questions_text(questions)
    comment = await ctx.tool(
        "add_comment",
        card_id=card_id,
        body="This request needs more detail before it can be triaged:\n" + text,
        confidence=analysis.get("confidence"),
        run_id=step_id,
    )
    result: dict[str, Any] = {"questions": questions, "comment_draft_id": comment["draft_id"], "comms_draft_id": None}

    if is_requested(state, "draft_comms"):
        context = await _context(ctx, state)
        run = await ctx.prompt(
            "draft_update",
            {
                "audience": "requester",
                "recommendation": "needs_info",
                "card_title": context["card"]["title"],
                "decision_summary": "We need more detail before we can decide. Questions:\n" + text,
            },
            card_id=card_id,
            parent_run_id=step_id,
        )
        out = run.output
        draft = await ctx.tool(
            "save_draft",
            card_id=card_id,
            audience="requester",
            subject=out.subject,
            body=out.body,
            tone=out.tone,
            confidence=out.confidence,
            run_id=run.run_id,
        )
        result["comms_draft_id"] = draft["draft_id"]
    return StepOutcome(result, {"outcome": "needs_info"})


# ---- group_problem ----------------------------------------------------------------------------
async def group_problem(ctx: GraphContext, state: WorkflowState, _inputs: dict, step_id: int) -> StepOutcome:
    card_id = state["card_id"]
    context = await _context(ctx, state)
    card = context["card"]
    problems = (await ctx.tool("list_problems", board_id=card["board_id"]))["problems"]
    run = await ctx.prompt(
        "group_problem",
        {
            "card_title": card["title"],
            "card_description": ctx.clip(card["description"]),
            "existing_problems": [
                {"id": p["id"], "title": p["title"][:200], "statement": p["statement"][:500]} for p in problems[:50]
            ],
        },
        card_id=card_id,
        parent_run_id=step_id,
    )
    out = run.output
    result: dict[str, Any] = {**_dump(out), "llm_run_id": run.run_id, "applied": "none"}
    if out.decision == "existing":
        if out.existing_problem_id not in {p["id"] for p in problems}:  # a model can invent an id
            raise ValueError(f"the model referred to problem {out.existing_problem_id}, which does not exist")
        await ctx.tool(
            "attach_problem",
            card_id=card_id,
            problem_id=out.existing_problem_id,
            confidence=out.confidence,
            rationale=out.rationale,
        )
        result["applied"] = "attached"
    elif out.decision == "new":
        saved = await ctx.tool(
            "save_suggestion",
            card_id=card_id,
            kind="problem",
            payload={
                "new_problem_title": out.new_problem_title,
                "new_problem_statement": out.new_problem_statement,
                "rationale": out.rationale,
            },
            confidence=out.confidence,
            run_id=run.run_id,
        )
        result.update(applied="proposed_new", draft_id=saved["draft_id"])
    return StepOutcome(result)


# ---- score ------------------------------------------------------------------------------------
async def score(ctx: GraphContext, state: WorkflowState, _inputs: dict, step_id: int) -> StepOutcome:
    card_id = state["card_id"]
    context = await _context(ctx, state)
    card = context["card"]
    rubric = await ctx.tool("get_rubric")
    # The model sees what each criterion means, never the weights: the maths is done by the tool.
    criteria = [{k: c[k] for k in ("key", "label", "description", "anchors")} for c in rubric["criteria"]]
    run = await ctx.prompt(
        "score_request",
        {
            "vote_count": card["vote_count"],
            "rubric": criteria,
            "card_title": card["title"],
            "card_description": ctx.clip(card["description"]),
            "feedback_quotes": _quotes(ctx, context),
        },
        card_id=card_id,
        parent_run_id=step_id,
    )
    out = run.output
    expected = {c["key"] for c in rubric["criteria"]}
    given = {c.key for c in out.criteria}
    if given != expected:
        raise ValueError(
            "the model's criteria do not match the rubric: "
            f"missing {sorted(expected - given)}, unexpected {sorted(given - expected)}"
        )
    saved = await ctx.tool(
        "save_scores",
        card_id=card_id,
        scores=[{"key": c.key, "rating": c.rating, "rationale": c.rationale} for c in out.criteria],
        run_id=run.run_id,
    )
    suggestion = await ctx.tool(
        "save_suggestion",
        card_id=card_id,
        kind="score",
        payload={
            "score_id": saved["score_id"],
            "total": saved["total"],
            "rubric": f"{saved['rubric_name']} v{saved['rubric_version']}",
            "lines": saved["lines"],
        },
        confidence=out.confidence,
        run_id=run.run_id,
    )
    result = {
        "ratings": {c.key: c.rating for c in out.criteria},
        "total": saved["total"],
        "scale_max": 100,
        "lines": saved["lines"],
        "score_id": saved["score_id"],
        "draft_id": suggestion["draft_id"],
        "confidence": out.confidence,
        "rationale": out.rationale,
        "llm_run_id": run.run_id,
    }
    return StepOutcome(result)


# ---- suggest_tags -----------------------------------------------------------------------------
async def suggest_tags(ctx: GraphContext, state: WorkflowState, _inputs: dict, step_id: int) -> StepOutcome:
    card_id = state["card_id"]
    context = await _context(ctx, state)
    card = context["card"]
    existing = [t["name"] for t in (await ctx.tool("list_tags", board_id=card["board_id"]))["tags"]]
    run = await ctx.prompt(
        "suggest_tags",
        {"card_title": card["title"], "card_description": ctx.clip(card["description"]), "existing_tags": existing},
        card_id=card_id,
        parent_run_id=step_id,
    )
    out = run.output
    tags: list[dict[str, Any]] = []
    for tag in out.tags:
        entry = {"name": tag.name, "reason": tag.reason}
        try:
            saved = await ctx.tool(
                "add_tag",
                card_id=card_id,
                tag=tag.name,
                reason=tag.reason,
                confidence=out.confidence,
                run_id=run.run_id,
            )
            entry["draft_id"] = saved["draft_id"]
        except ToolException as exc:  # for example "already has the tag": not worth failing the step
            entry["skipped"] = str(exc)
        tags.append(entry)
    return StepOutcome(
        {"tags": tags, "confidence": out.confidence, "rationale": out.rationale, "llm_run_id": run.run_id}
    )


# ---- decision_brief ---------------------------------------------------------------------------
async def decision_brief(ctx: GraphContext, state: WorkflowState, inputs: dict, step_id: int) -> StepOutcome:
    card_id = state["card_id"]
    context = await _context(ctx, state)
    card = context["card"]
    analysis, scored, related = inputs["analyze_need"], inputs["score"], inputs.get("find_related")
    related_requests = [
        {"card_id": c["card_id"], "title": c["title"], "verdict": c["verdict"], "confidence": c["confidence"]}
        for c in (related or {}).get("candidates", [])
        if c.get("action") in ("merge", "link_related", "review")
    ]
    run = await ctx.prompt(
        "decision_brief",
        {
            "vote_count": card["vote_count"],
            "score_summary": {
                "total": scored["total"],
                "scale_max": scored.get("scale_max", 100),
                "ratings": scored["ratings"],
            },
            "card_title": card["title"],
            "card_description": ctx.clip(card["description"]),
            "analysis": {
                k: analysis.get(k)
                for k in ("underlying_need", "problem_statement", "is_actionable", "clarifying_questions")
            },
            "related_requests": related_requests,
        },
        card_id=card_id,
        parent_run_id=step_id,
    )
    out = run.output
    saved = await ctx.tool(
        "save_suggestion",
        card_id=card_id,
        kind="brief",
        payload=_dump(out),
        confidence=out.confidence,
        run_id=run.run_id,
    )
    return StepOutcome({**_dump(out), "llm_run_id": run.run_id, "draft_id": saved["draft_id"]})


# ---- draft_comms ------------------------------------------------------------------------------
async def draft_comms(ctx: GraphContext, state: WorkflowState, inputs: dict, step_id: int) -> StepOutcome:
    card_id = state["card_id"]
    context = await _context(ctx, state)
    brief = inputs["decision_brief"]
    drafts: list[dict[str, Any]] = []
    for audience in ctx.config.comms_audiences:
        run = await ctx.prompt(
            "draft_update",
            {
                "audience": audience,
                "recommendation": brief["recommendation"],
                "card_title": context["card"]["title"],
                "decision_summary": brief["summary"],
            },
            card_id=card_id,
            parent_run_id=step_id,
        )
        out = run.output
        saved = await ctx.tool(
            "save_draft",
            card_id=card_id,
            audience=audience,
            subject=out.subject,
            body=out.body,
            tone=out.tone,
            confidence=out.confidence,
            run_id=run.run_id,
        )
        drafts.append(
            {"audience": audience, "subject": out.subject, "draft_id": saved["draft_id"], "created": saved["created"]}
        )
    return StepOutcome({"drafts": drafts})


NODES: dict[str, tuple[Body, bool]] = {
    # name: (body, selectable). Routes are not steps a caller can ask for.
    "validate_intake": (validate_intake, True),
    "analyze_need": (analyze_need, True),
    "find_related": (find_related, True),
    "human_checkpoint": (human_checkpoint, False),
    "needs_info": (needs_info, False),
    "merge_proposal": (merge_proposal, False),
    "group_problem": (group_problem, True),
    "score": (score, True),
    "suggest_tags": (suggest_tags, True),
    "decision_brief": (decision_brief, True),
    "draft_comms": (draft_comms, True),
}

assert set(STEPS) <= set(NODES)
