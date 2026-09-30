"""End-to-end runs in mock mode: real MCP server processes, the real graph, the mock model."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from ai.graph.state import STEPS
from core.models import CardLink, Draft, DraftKind, DraftStatus, LinkKind, LinkStatus, RunStatus, Score
from tests.graph.conftest import (
    DISTINCT,
    DUPLICATE_OF_SAML,
    VAGUE,
    ScriptedLLM,
    add_card,
    card_snapshot,
    runs,
    step_rows,
)

pytestmark = pytest.mark.anyio


def drafts(db, card_id, kind=None):
    with db.session() as s:
        stmt = select(Draft).where(Draft.card_id == card_id).order_by(Draft.id)
        if kind:
            stmt = stmt.where(Draft.kind == kind)
        return list(s.scalars(stmt))


def links(db):
    with db.session() as s:
        return list(s.scalars(select(CardLink).order_by(CardLink.id)))


# ---- the full run -----------------------------------------------------------------------------
async def test_full_run_executes_every_step_and_stores_suggestions(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    before = card_snapshot(db, card_id)
    async with make_workflow() as wf:
        report = await wf.start(card_id)

    assert (report.status, report.outcome) == ("succeeded", "completed")
    assert report.steps == {step: "succeeded" for step in STEPS}
    assert report.errors == [] and report.interrupt is None
    assert report.results["validate_intake"]["valid"] is True
    assert report.results["analyze_need"]["is_actionable"] is True
    assert 0 <= report.results["score"]["total"] <= 100
    assert report.results["decision_brief"]["recommendation"] in ("plan", "review", "decline", "needs_info")

    kinds = [s["kind"] for s in report.suggestions]
    assert {"analysis", "score", "brief", "comms", "tag"} <= set(kinds)
    assert kinds.count("comms") == 1
    assert all(s["status"] == "pending" for s in report.suggestions)

    # What was stored is what the tools would store: a score row, a tag *suggestion*, nothing applied.
    with db.session() as s:
        (score,) = s.scalars(select(Score).where(Score.card_id == card_id)).all()
        assert score.total == report.results["score"]["total"]
    (tag,) = drafts(db, card_id, DraftKind.tag)
    assert tag.payload["name"] == "security" and tag.payload["is_existing"] is True
    assert card_snapshot(db, card_id) == before  # the card itself is untouched


async def test_every_node_writes_its_status_and_model_calls_hang_off_the_step(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    async with make_workflow() as wf:
        report = await wf.start(card_id)

    (workflow,) = runs(db, kind="workflow")
    assert workflow.id == report.run_id and workflow.status is RunStatus.succeeded
    assert workflow.output["outcome"] == "completed" and workflow.finished_at is not None

    steps = step_rows(db, workflow.id)
    assert set(steps) == set(STEPS)
    assert all(r.status is RunStatus.succeeded and r.finished_at and r.latency_ms is not None for r in steps.values())
    assert all(r.card_id == card_id and r.parent_run_id == workflow.id for r in steps.values())
    assert steps["score"].output["total"] == report.results["score"]["total"]

    calls = {r.kind: r for r in runs(db) if r.parent_run_id in {s.id for s in steps.values()}}
    expected = {"analyze_need", "group_problem", "score_request", "suggest_tags", "decision_brief", "draft_update"}
    assert expected <= set(calls)
    for call in calls.values():
        assert (
            call.model == "mock" and call.input_hash and call.prompt_version == 2 and call.status is RunStatus.succeeded
        )
    assert calls["score_request"].parent_run_id == steps["score"].id


async def test_the_run_is_repeatable_and_does_not_pile_up_identical_suggestions(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    async with make_workflow() as wf:
        first = await wf.start(card_id)
        second = await wf.start(card_id)
    assert first.run_id != second.run_id and second.status == "succeeded"
    # The mock is deterministic, so the second run's tag/comms/brief suggestions equal the first's and are reused.
    assert len(drafts(db, card_id, DraftKind.tag)) == 1
    assert len(drafts(db, card_id, DraftKind.brief)) == 1
    assert len(drafts(db, card_id, DraftKind.comms)) == 1
    # validate_intake warns that there are still suggestions waiting.
    assert any("still waiting" in w for w in second.results["validate_intake"]["warnings"])


# ---- partial runs -----------------------------------------------------------------------------
async def test_only_score_runs_only_score(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    llm = ScriptedLLM()
    async with make_workflow(llm=llm) as wf:
        report = await wf.start(card_id, steps="score")

    assert llm.calls == ["score_request"]  # the model was asked about nothing else
    assert report.steps == {step: ("succeeded" if step == "score" else "skipped") for step in STEPS}
    assert list(report.results) == ["score"]
    assert {s["kind"] for s in report.suggestions} == {"score"}
    assert [r.kind for r in runs(db) if r.kind in ("analyze_need", "judge_duplicate", "decision_brief")] == []
    skipped = {n: r for n, r in step_rows(db, report.run_id).items() if r.status is RunStatus.skipped}
    assert set(skipped) == set(STEPS) - {"score"}
    assert all(r.output == {"reason": "not requested"} for r in skipped.values())
    with db.session() as s:
        assert s.scalar(select(func.count(Score.id)).where(Score.card_id == card_id)) == 1


async def test_steps_can_be_given_as_names_aliases_or_a_list(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    async with make_workflow() as wf:
        by_alias = await wf.start(card_id, steps="analyze,tags")
        by_list = await wf.start(card_id, steps=["suggest_tags", "analyze_need"])
    assert {s for s, v in by_alias.steps.items() if v == "succeeded"} == {"analyze_need", "suggest_tags"}
    assert {s for s, v in by_list.steps.items() if v == "succeeded"} == {"analyze_need", "suggest_tags"}
    with pytest.raises(ValueError, match="Unknown step"):
        async with make_workflow() as wf:
            await wf.start(card_id, steps="analyze,teleport")


async def test_a_step_that_needs_earlier_results_loads_them_from_a_previous_run(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    async with make_workflow() as wf:
        earlier = await wf.start(card_id, steps="analyze,score")
        later = await wf.start(card_id, steps="brief")

    assert later.steps["decision_brief"] == "succeeded"
    assert list(later.results) == ["decision_brief"]  # analysis and score were loaded, not re-run
    assert [r for r in runs(db, parent_run_id=later.run_id) if r.kind == "step:analyze_need"][
        0
    ].status is RunStatus.skipped
    # the brief was written from the earlier run's score
    (brief,) = [s for s in later.suggestions if s["kind"] == "brief"]
    total = earlier.results["score"]["total"]
    assert f"Score ratio {total / 100:.2f}" in brief["payload"]["summary"]


async def test_a_step_without_its_dependencies_is_skipped_with_the_reason(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    async with make_workflow() as wf:
        alone = await wf.start(card_id, steps="brief")
        comms = await wf.start(card_id, steps="comms")
    assert alone.steps["decision_brief"] == "skipped" and alone.outcome == "completed"
    reason = step_rows(db, alone.run_id)["decision_brief"].output["reason"]
    assert reason.startswith("missing dependency: analyze_need")
    assert comms.steps["draft_comms"] == "skipped"
    assert step_rows(db, comms.run_id)["draft_comms"].output["reason"].startswith("missing dependency: decision_brief")
    assert drafts(db, card_id) == []


# ---- routes -----------------------------------------------------------------------------------
async def test_a_vague_request_routes_to_needs_info(db, make_workflow):
    card_id = add_card(db, *VAGUE)
    async with make_workflow() as wf:
        report = await wf.start(card_id)

    assert (report.status, report.outcome) == ("succeeded", "needs_info")
    assert report.results["analyze_need"]["is_actionable"] is False
    assert report.steps["analyze_need"] == "succeeded" and report.steps["validate_intake"] == "succeeded"
    assert all(report.steps[s] == "skipped" for s in STEPS[2:])
    rows = step_rows(db, report.run_id)
    assert rows["score"].output["reason"] == "not reached: the run ended early (needs_info)"
    assert rows["needs_info"].status is RunStatus.succeeded

    (comment,) = drafts(db, card_id, DraftKind.comment)
    assert comment.status is DraftStatus.pending and "needs more detail" in comment.payload["body"]
    assert all(q in comment.payload["body"] for q in report.results["needs_info"]["questions"])
    (comms,) = drafts(db, card_id, DraftKind.comms)  # a full run also drafts the message to the requester
    assert comms.payload["audience"] == "requester" and "more detail" in comms.payload["body"]
    assert drafts(db, card_id, DraftKind.score) == [] and links(db) == []


async def test_needs_info_only_drafts_the_message_when_comms_were_requested(db, make_workflow):
    card_id = add_card(db, *VAGUE)
    async with make_workflow() as wf:
        report = await wf.start(card_id, steps="analyze")
    assert report.outcome == "needs_info" and report.results["needs_info"]["comms_draft_id"] is None
    assert len(drafts(db, card_id, DraftKind.comment)) == 1 and drafts(db, card_id, DraftKind.comms) == []


async def test_a_confident_duplicate_routes_to_a_merge_proposal(db, make_workflow):
    duplicate_id = add_card(db, *DUPLICATE_OF_SAML)
    saml = db.ids["saml"]
    async with make_workflow() as wf:
        report = await wf.start(duplicate_id)

    assert (report.status, report.outcome) == ("succeeded", "duplicate_proposed")
    found = report.results["find_related"]
    assert found["merge"]["card_id"] == saml and found["merge"]["confidence"] >= 0.85
    assert all(report.steps[s] == "skipped" for s in STEPS[3:])  # nothing is scored for a duplicate

    (merge,) = drafts(db, duplicate_id, DraftKind.link)
    assert merge.status is DraftStatus.pending
    assert merge.payload["action"] == "merge" and merge.payload["human_confirmed"] is False
    assert (merge.payload["keep_card_id"], merge.payload["merge_card_id"]) == (
        saml,
        duplicate_id,
    )  # the older card stays
    assert merge.payload["duplicate_of"] == saml and merge.run_id == step_rows(db, report.run_id)["merge_proposal"].id

    duplicate_link = next(link for link in links(db) if link.kind is LinkKind.duplicate)
    assert (duplicate_link.card_id, duplicate_link.other_card_id) == tuple(sorted((saml, duplicate_id)))
    assert duplicate_link.status is LinkStatus.suggested and duplicate_link.confidence == found["merge"]["confidence"]


async def test_related_cards_get_a_suggested_related_link_above_the_threshold(db, make_workflow):
    async with make_workflow() as wf:
        report = await wf.start(db.ids["saml"], steps="related")  # the fixture's okta card is related, not a duplicate
    judged = {c["card_id"]: c for c in report.results["find_related"]["candidates"] if c.get("verdict")}
    assert judged[db.ids["okta"]]["verdict"] == "related" and judged[db.ids["okta"]]["action"] == "link_related"
    (link,) = [link for link in links(db) if db.ids["okta"] in (link.card_id, link.other_card_id)]
    assert (link.kind, link.status) == (LinkKind.related, LinkStatus.suggested)
    assert report.outcome == "completed"


@pytest.mark.parametrize(
    "config_change, expect_links",
    [({}, True), ({"link_threshold": 0.95, "merge_threshold": 0.95}, False)],
)
async def test_the_link_threshold_is_configurable(db, make_workflow, config_change, expect_links):
    from ai.graph.state import GraphConfig
    from tests.graph.conftest import TEST_CONFIG

    duplicate_id = add_card(db, *DUPLICATE_OF_SAML)
    config = GraphConfig(**{**TEST_CONFIG.__dict__, **config_change})
    async with make_workflow(config=config) as wf:
        report = await wf.start(duplicate_id, steps="related")
    assert bool(links(db)) is expect_links
    assert (report.outcome == "duplicate_proposed") is expect_links  # below the threshold nothing is said at all


async def test_a_confirmed_link_is_not_judged_again(db, make_workflow):
    duplicate_id = add_card(db, *DUPLICATE_OF_SAML)
    low, high = sorted((duplicate_id, db.ids["saml"]))
    with db.session() as s:
        s.add(CardLink(card_id=low, other_card_id=high, kind=LinkKind.duplicate, status=LinkStatus.confirmed))
        s.commit()
    async with make_workflow() as wf:
        report = await wf.start(duplicate_id, steps="related")
    entry = next(c for c in report.results["find_related"]["candidates"] if c["card_id"] == db.ids["saml"])
    assert entry["action"] == "skip_confirmed" and "verdict" not in entry
    assert report.outcome == "completed"
