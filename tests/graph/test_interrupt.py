"""Pausing at a human checkpoint and resuming, including from a different Workflow (a new process)."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, func, select

from ai.graph.state import STEPS, GraphConfig
from core.models import CardLink, Draft, DraftKind, LinkKind, LinkStatus, RunStatus
from tests.graph.conftest import DUPLICATE_OF_SAML, TEST_CONFIG, VAGUE, add_card, runs, step_rows

pytestmark = pytest.mark.anyio

# The mock judge is at most 0.9 sure, so a 0.95 merge threshold makes every duplicate "unsure".
UNSURE_DUPLICATES = GraphConfig(**{**TEST_CONFIG.__dict__, "merge_threshold": 0.95})
# The mock is 0.8 sure about an actionable analysis, so a 0.9 floor makes it ask a person.
UNSURE_ANALYSIS = GraphConfig(**{**TEST_CONFIG.__dict__, "analysis_min_confidence": 0.9})


def links(db):
    with db.session() as s:
        return list(s.scalars(select(CardLink).order_by(CardLink.id)))


def drafts(db, card_id, kind):
    with db.session() as s:
        return list(s.scalars(select(Draft).where(Draft.card_id == card_id, Draft.kind == kind).order_by(Draft.id)))


async def test_an_unsure_duplicate_pauses_the_run_for_a_person(db, make_workflow):
    card_id = add_card(db, *DUPLICATE_OF_SAML)
    async with make_workflow(config=UNSURE_DUPLICATES) as wf:
        report = await wf.start(card_id)

    assert report.status == "awaiting_human" and report.outcome is None
    question = report.interrupt
    assert (
        question["kind"] == "confirm_duplicate"
        and question["run_id"] == report.run_id
        and question["card_id"] == card_id
    )
    (candidate,) = [c for c in question["payload"]["candidates"] if c["card_id"] == db.ids["saml"]]
    assert 0.6 <= candidate["confidence"] < 0.95 and candidate["rationale"]

    # Nothing was decided on the person's behalf: no link, no merge proposal, nothing after find_related.
    assert [link for link in links(db) if link.kind is LinkKind.duplicate] == []
    assert drafts(db, card_id, DraftKind.link) == []
    assert set(report.steps) == {"validate_intake", "analyze_need", "find_related"}
    (workflow,) = runs(db, kind="workflow")
    assert workflow.status is RunStatus.awaiting_human and workflow.finished_at is None
    checkpoint = step_rows(db, report.run_id)["human_checkpoint"]
    assert checkpoint.status is RunStatus.awaiting_human


async def test_resuming_from_a_new_process_confirms_the_duplicate(db, make_workflow, tmp_path):
    card_id = add_card(db, *DUPLICATE_OF_SAML)
    checkpoint = tmp_path / "shared-checkpoints.db"
    async with make_workflow(config=UNSURE_DUPLICATES, checkpoint=checkpoint) as first:
        paused = await first.start(card_id)
    assert paused.status == "awaiting_human"
    # The first Workflow, its MCP servers and its database connection are gone. Only files remain.

    async with make_workflow(config=UNSURE_DUPLICATES, checkpoint=checkpoint) as second:
        again = await second.report(paused.run_id)
        assert again.interrupt == paused.interrupt and again.status == "awaiting_human"
        done = await second.resume(paused.run_id, {"verdict": "duplicate"})

    assert (done.status, done.outcome, done.interrupt) == ("succeeded", "duplicate_proposed", None)
    (decision,) = done.human_decisions
    assert decision["checkpoint"] == "confirm_duplicate"
    verdicts = {d["card_id"]: d["verdict"] for d in decision["decision"]["decisions"]}
    assert verdicts[db.ids["saml"]] == "duplicate" and set(verdicts.values()) == {"duplicate"}
    (merge,) = drafts(db, card_id, DraftKind.link)
    assert merge.payload["action"] == "merge" and merge.payload["human_confirmed"] is True
    (link,) = [link for link in links(db) if link.kind is LinkKind.duplicate]
    assert link.status is LinkStatus.suggested and "a person judged this duplicate" in link.rationale
    (workflow,) = runs(db, kind="workflow")
    assert workflow.status is RunStatus.succeeded and workflow.output["outcome"] == "duplicate_proposed"
    assert done.steps["human_checkpoint"] == "succeeded"
    assert len([r for r in runs(db, parent_run_id=paused.run_id) if r.kind == "step:human_checkpoint"]) == 1


async def test_saying_distinct_lets_the_run_carry_on_without_a_link(db, make_workflow):
    card_id = add_card(db, *DUPLICATE_OF_SAML)
    async with make_workflow(config=UNSURE_DUPLICATES) as wf:
        paused = await wf.start(card_id)
        done = await wf.resume(paused.run_id, {"verdict": "distinct"})
    assert (done.status, done.outcome) == ("succeeded", "completed")
    assert all(done.steps[s] == "succeeded" for s in STEPS)  # the rest of the graph ran after the answer
    assert [link for link in links(db) if link.kind is LinkKind.duplicate] == []
    assert drafts(db, card_id, DraftKind.link) == []


async def test_a_person_can_decide_each_candidate_separately(db, make_workflow):
    card_id = add_card(db, *DUPLICATE_OF_SAML)
    async with make_workflow(config=UNSURE_DUPLICATES) as wf:
        paused = await wf.start(card_id)
        candidates = [c["card_id"] for c in paused.interrupt["payload"]["candidates"]]
        decisions = [{"card_id": c, "verdict": "related"} for c in candidates]
        done = await wf.resume(paused.run_id, {"decisions": decisions})
    assert done.outcome == "completed"
    assert {link.kind for link in links(db)} == {LinkKind.related}
    assert len(links(db)) == len(candidates)


async def test_an_invalid_answer_asks_again_with_an_explanation(db, make_workflow):
    card_id = add_card(db, *DUPLICATE_OF_SAML)
    async with make_workflow(config=UNSURE_DUPLICATES) as wf:
        paused = await wf.start(card_id)
        retry = await wf.resume(paused.run_id, {"verdict": "maybe"})
        assert retry.status == "awaiting_human" and "verdict" in retry.interrupt["error"]
        assert links(db) == []
        done = await wf.resume(paused.run_id, {"verdict": "distinct"})
    assert done.status == "succeeded" and "error" not in (done.interrupt or {})


async def test_a_low_confidence_analysis_asks_a_person_to_confirm(db, make_workflow):
    card_id = add_card(db, "Offline mode for field technicians", "Technicians work in basements without signal.")
    async with make_workflow(config=UNSURE_ANALYSIS) as wf:
        paused = await wf.start(card_id)
        assert paused.status == "awaiting_human" and paused.interrupt["kind"] == "confirm_actionable"
        assert paused.interrupt["payload"]["analysis"]["confidence"] < 0.9
        assert "find_related" not in paused.steps
        done = await wf.resume(paused.run_id, {"actionable": True})
    assert (done.status, done.outcome) == ("succeeded", "completed")
    assert all(done.steps[s] == "succeeded" for s in STEPS)
    assert done.human_decisions == [{"checkpoint": "confirm_actionable", "decision": {"actionable": True}}]


async def test_a_person_can_overrule_the_analysis_and_ask_for_more_detail(db, make_workflow):
    card_id = add_card(db, "Offline mode for field technicians", "Technicians work in basements without signal.")
    async with make_workflow(config=UNSURE_ANALYSIS) as wf:
        paused = await wf.start(card_id)
        done = await wf.resume(paused.run_id, {"actionable": False})
    assert done.outcome == "needs_info" and done.results["needs_info"]["comment_draft_id"]
    assert done.steps["find_related"] == "skipped" and len(drafts(db, card_id, DraftKind.comment)) == 1


async def test_a_vague_request_does_not_pause_even_when_the_model_is_unsure(db, make_workflow):
    card_id = add_card(db, *VAGUE)
    async with make_workflow(config=UNSURE_ANALYSIS) as wf:
        report = await wf.start(card_id)
    assert report.status == "succeeded" and report.outcome == "needs_info" and report.interrupt is None


async def test_resume_rejects_runs_that_are_not_waiting_or_do_not_exist(db, make_workflow):
    card_id = add_card(db, "Offline mode for field technicians", "Technicians work in basements without signal.")
    async with make_workflow() as wf:
        finished = await wf.start(card_id)
        with pytest.raises(ValueError, match="not waiting for an answer"):
            await wf.resume(finished.run_id, {"actionable": True})
        with pytest.raises(ValueError, match="No run 424242"):
            await wf.resume(424242, {"actionable": True})
        with pytest.raises(ValueError, match="No run 424242"):
            await wf.report(424242)


async def test_two_paused_runs_do_not_interfere(db, make_workflow):
    a = add_card(db, *DUPLICATE_OF_SAML)
    b = add_card(db, "Offline mode for field technicians", "Technicians work in basements without signal.")
    async with make_workflow(config=UNSURE_DUPLICATES) as wf:
        first = await wf.start(a)
        second = await wf.start(b)
        assert first.status == "awaiting_human" and second.status == "succeeded"
        done = await wf.resume(first.run_id, {"verdict": "duplicate"})
    assert done.card_id == a and done.outcome == "duplicate_proposed"
    with db.session() as s:
        assert s.scalar(select(func.count(Draft.id)).where(Draft.card_id == b, Draft.kind == DraftKind.link)) == 0


async def test_a_reused_run_id_never_picks_up_an_old_checkpoint(db, make_workflow, tmp_path):
    """Run ids can repeat (the seed script wipes the database) while checkpoints persist in their own file."""
    from core.models import AIRun

    checkpoint = tmp_path / "stale-checkpoints.db"
    first_card = add_card(db, "Offline mode for field technicians", "Technicians work in basements without signal.")
    async with make_workflow(checkpoint=checkpoint) as wf:
        old = await wf.start(first_card)
    assert old.status == "succeeded"

    with db.session() as s:  # simulate a wiped database: the next run gets the same id again
        s.execute(delete(AIRun))
        s.add(AIRun(id=old.run_id - 1, kind="filler"))  # the next id handed out is max + 1
        s.commit()
    second_card = add_card(db, *VAGUE)
    async with make_workflow(checkpoint=checkpoint) as wf:
        new = await wf.start(second_card)

    assert new.run_id == old.run_id  # the ids really collided
    assert new.card_id == second_card and new.outcome == "needs_info"  # and the new run is its own run
    assert "score" not in new.results  # nothing leaked in from the old run's state
