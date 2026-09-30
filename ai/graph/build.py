"""Assembles the graph.

    validate_intake -> analyze_need -> find_related -> group_problem -> score
                    -> suggest_tags -> decision_brief -> draft_comms -> finalize

Routes (conditional edges) branch off that line:

    analyze_need   vague request            -> needs_info -> finalize
                   model unsure             -> human_checkpoint -> (find_related | needs_info)
    find_related   confident duplicate      -> merge_proposal -> finalize
                   unsure duplicate         -> human_checkpoint -> (group_problem | merge_proposal)
    validate_intake stop (card not found)   -> finalize

Every path ends in `finalize`, which accounts for every step (run, skipped or not reached) and
closes the run's AIRun row.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from ai.graph.context import GraphContext
from ai.graph.nodes import NODES, is_requested, make_node
from ai.graph.state import STEPS, WorkflowState
from core.models import RunStatus


def after_validate(state: WorkflowState) -> str:
    return "finalize" if state.get("route") == "stop" else "analyze_need"


def after_analysis(state: WorkflowState) -> str:
    return {"needs_info": "needs_info", "checkpoint": "human_checkpoint"}.get(state.get("route", ""), "find_related")


def after_related(state: WorkflowState) -> str:
    return {"merge": "merge_proposal", "checkpoint": "human_checkpoint"}.get(state.get("route", ""), "group_problem")


def after_checkpoint(state: WorkflowState) -> str:
    route = state.get("route")
    if route == "needs_info":
        return "needs_info"
    if route == "merge":
        return "merge_proposal"
    return state.get("resume_to", "group_problem")


def make_finalize(ctx: GraphContext):
    async def finalize(state: WorkflowState) -> dict[str, Any]:
        run_id, card_id = state["run_id"], state["card_id"]
        statuses = dict(state.get("statuses") or {})
        errors = list(state.get("errors") or [])
        outcome = state.get("outcome")

        added: dict[str, str] = {}
        for step in STEPS:
            if step in statuses:
                continue
            if is_requested(state, step):
                reason = f"not reached: the run ended early ({outcome or 'stopped'})"
            else:
                reason = "not requested"
            ctx.record_skipped(run_id, card_id, step, reason)
            added[step] = "skipped"
        final_statuses = {**statuses, **added}

        if outcome is None:
            outcome = "failed" if errors and "succeeded" not in final_statuses.values() else "completed"
        failed = outcome == "failed"
        ctx.finish_step(
            run_id,
            RunStatus.failed if failed else RunStatus.succeeded,
            output={
                "outcome": outcome,
                "statuses": final_statuses,
                "errors": errors,
                "human_decisions": state.get("human_decisions") or [],
            },
            error="; ".join(f"{e['step']}: {e['error']}" for e in errors)[:2000] if failed else None,
        )
        return {"outcome": outcome, "statuses": added}

    return finalize


def build_graph(ctx: GraphContext) -> StateGraph:
    graph = StateGraph(WorkflowState)
    for name, (body, selectable) in NODES.items():
        graph.add_node(name, make_node(ctx, name, body, selectable=selectable))
    graph.add_node("finalize", make_finalize(ctx))

    graph.add_edge(START, "validate_intake")
    graph.add_conditional_edges(
        "validate_intake", after_validate, {"analyze_need": "analyze_need", "finalize": "finalize"}
    )
    graph.add_conditional_edges(
        "analyze_need",
        after_analysis,
        {"find_related": "find_related", "needs_info": "needs_info", "human_checkpoint": "human_checkpoint"},
    )
    graph.add_conditional_edges(
        "find_related",
        after_related,
        {"group_problem": "group_problem", "merge_proposal": "merge_proposal", "human_checkpoint": "human_checkpoint"},
    )
    graph.add_conditional_edges(
        "human_checkpoint",
        after_checkpoint,
        {
            "find_related": "find_related",
            "group_problem": "group_problem",
            "needs_info": "needs_info",
            "merge_proposal": "merge_proposal",
        },
    )
    graph.add_edge("group_problem", "score")
    graph.add_edge("score", "suggest_tags")
    graph.add_edge("suggest_tags", "decision_brief")
    graph.add_edge("decision_brief", "draft_comms")
    graph.add_edge("draft_comms", "finalize")
    graph.add_edge("needs_info", "finalize")
    graph.add_edge("merge_proposal", "finalize")
    graph.add_edge("finalize", END)
    return graph
