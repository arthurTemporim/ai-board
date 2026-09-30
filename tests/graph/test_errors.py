"""A failing node records its error and the graph continues wherever that is safe."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from ai.graph.state import STEPS
from ai.llm.errors import LLMCallError
from core.models import Draft, DraftKind, RunStatus, Score
from tests.graph.conftest import DISTINCT, DUPLICATE_OF_SAML, ScriptedLLM, add_card, runs, step_rows

pytestmark = pytest.mark.anyio


def boom(message: str):
    def hook(_prompt):
        raise LLMCallError(message)

    return hook


def drafts(db, card_id, kind):
    with db.session() as s:
        return list(s.scalars(select(Draft).where(Draft.card_id == card_id, Draft.kind == kind)))


async def test_a_failing_step_is_recorded_and_independent_steps_still_run(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    llm = ScriptedLLM({"suggest_tags": boom("model unavailable")})
    async with make_workflow(llm=llm) as wf:
        report = await wf.start(card_id)

    assert report.status == "succeeded" and report.outcome == "completed"  # the run still finishes
    assert report.steps["suggest_tags"] == "failed"
    assert {s: v for s, v in report.steps.items() if s != "suggest_tags"} == {
        s: "succeeded" for s in STEPS if s != "suggest_tags"
    }
    assert report.errors == [{"step": "suggest_tags", "error": "LLMCallError: model unavailable"}]
    row = step_rows(db, report.run_id)["suggest_tags"]
    assert row.status is RunStatus.failed and row.error == "LLMCallError: model unavailable" and row.finished_at
    # the model call that failed is logged too, under the step
    (call,) = [r for r in runs(db, kind="suggest_tags")]
    assert call.status is RunStatus.failed and call.parent_run_id == row.id and "model unavailable" in call.error
    assert drafts(db, card_id, DraftKind.tag) == [] and len(drafts(db, card_id, DraftKind.brief)) == 1


async def test_steps_that_depend_on_a_failed_step_are_skipped_not_run_on_missing_input(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    llm = ScriptedLLM({"score_request": boom("timeout")})
    async with make_workflow(llm=llm) as wf:
        report = await wf.start(card_id)

    assert report.steps["score"] == "failed"
    assert report.steps["decision_brief"] == "skipped" and report.steps["draft_comms"] == "skipped"
    rows = step_rows(db, report.run_id)
    assert rows["decision_brief"].output["reason"] == "dependency failed: score"
    assert rows["draft_comms"].output["reason"].startswith("missing dependency: decision_brief")
    assert "decision_brief" not in llm.calls and "draft_update" not in llm.calls
    for step in ("analyze_need", "find_related", "group_problem", "suggest_tags"):
        assert report.steps[step] == "succeeded"
    with db.session() as s:
        assert s.scalars(select(Score)).all() == []


async def test_a_failed_analysis_does_not_stop_the_other_steps(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    llm = ScriptedLLM({"analyze_need": boom("bad gateway")})
    async with make_workflow(llm=llm) as wf:
        report = await wf.start(card_id)
    assert report.steps["analyze_need"] == "failed"
    assert report.steps["find_related"] == report.steps["group_problem"] == report.steps["score"] == "succeeded"
    assert report.steps["decision_brief"] == "skipped"  # it needs the analysis
    assert report.outcome == "completed"


async def test_a_model_inventing_a_problem_id_is_caught(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    llm = ScriptedLLM(
        {
            "group_problem": lambda p: {
                "decision": "existing",
                "existing_problem_id": 999,
                "confidence": 0.9,
                "rationale": "r",
            }
        }
    )
    async with make_workflow(llm=llm) as wf:
        report = await wf.start(card_id, steps="group")
    assert report.steps["group_problem"] == "failed"
    assert "problem 999, which does not exist" in report.errors[0]["error"]
    with db.session() as s:
        from core.models import CardProblem

        assert s.scalars(select(CardProblem)).all() == []  # nothing was attached


async def test_a_real_problem_id_is_attached_as_a_suggestion(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    llm = ScriptedLLM(
        {
            "group_problem": lambda p: {
                "decision": "existing",
                "existing_problem_id": db.ids["problem"],
                "confidence": 0.8,
                "rationale": "r",
            }
        }
    )
    async with make_workflow(llm=llm) as wf:
        report = await wf.start(card_id, steps="group")
    assert report.results["group_problem"]["applied"] == "attached"
    from core.models import CardProblem, LinkStatus

    with db.session() as s:
        row = s.get(CardProblem, (card_id, db.ids["problem"]))
        assert row.status is LinkStatus.suggested and row.confidence == 0.8


async def test_scores_for_the_wrong_criteria_are_rejected_before_they_are_saved(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    llm = ScriptedLLM(
        {
            "score_request": lambda p: {
                "criteria": [{"key": "charisma", "rating": 5, "rationale": "r"}],
                "confidence": 0.9,
                "rationale": "r",
            }
        }
    )
    async with make_workflow(llm=llm) as wf:
        report = await wf.start(card_id, steps="score")
    assert report.steps["score"] == "failed"
    assert "do not match the rubric" in report.errors[0]["error"] and "charisma" in report.errors[0]["error"]
    with db.session() as s:
        assert s.scalars(select(Score)).all() == []


async def test_one_bad_candidate_does_not_lose_the_others(db, make_workflow):
    card_id = add_card(db, *DUPLICATE_OF_SAML)
    calls = {"n": 0}

    def flaky(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMCallError("one-off failure")
        return (
            ScriptedLLM().hooks.get("x", None)
            or __import__("ai.llm.mock", fromlist=["MockLLM"]).MockLLM().invoke(prompt).output
        )

    async with make_workflow(llm=ScriptedLLM({"judge_duplicate": flaky})) as wf:
        report = await wf.start(card_id, steps="related")

    candidates = report.results["find_related"]["candidates"]
    assert [c["action"] for c in candidates].count("error") == 1
    assert len(candidates) >= 2 and any(c.get("verdict") for c in candidates)
    assert report.steps["find_related"] == "succeeded"
    assert any("one-off failure" in e["error"] for e in report.errors)


async def test_a_card_that_does_not_exist_stops_the_run(db, make_workflow):
    async with make_workflow() as wf:
        report = await wf.start(424242)
    assert (report.status, report.outcome) == ("failed", "failed")
    assert report.steps["validate_intake"] == "failed"
    assert all(report.steps[s] == "skipped" for s in STEPS[1:])
    assert "Card 424242 not found" in report.errors[0]["error"]
    (workflow,) = runs(db, kind="workflow")
    assert workflow.status is RunStatus.failed and "not found" in workflow.error
    assert step_rows(db, report.run_id)["score"].output["reason"] == "not reached: the run ended early (failed)"


async def test_a_partial_run_on_a_missing_card_fails_instead_of_pretending_to_succeed(db, make_workflow):
    async with make_workflow() as wf:
        report = await wf.start(424242, steps="score")
    assert report.steps["score"] == "failed" and report.outcome == "failed" and report.status == "failed"
    assert "not found" in report.errors[0]["error"]


async def test_an_unexpected_exception_in_a_node_is_contained(db, make_workflow):
    card_id = add_card(db, *DISTINCT)

    def crash(_prompt):
        raise RuntimeError("something unforeseen")

    async with make_workflow(llm=ScriptedLLM({"decision_brief": crash})) as wf:
        report = await wf.start(card_id)
    assert (
        report.steps["decision_brief"] == "failed" and "RuntimeError: something unforeseen" in report.errors[0]["error"]
    )
    assert report.steps["draft_comms"] == "skipped" and report.status == "succeeded"
