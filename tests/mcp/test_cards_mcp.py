from __future__ import annotations

import pytest
from sqlalchemy import func, select

from core.models import (
    Card,
    CardLink,
    CardProblem,
    CardTag,
    Comment,
    Draft,
    DraftKind,
    DraftStatus,
    LinkKind,
    LinkStatus,
)
from tests.mcp.conftest import CARDS_MODULE, tools_for

pytestmark = pytest.mark.anyio


def drafts(db, card_id, kind=None):
    with db.session() as s:
        stmt = select(Draft).where(Draft.card_id == card_id).order_by(Draft.id)
        if kind:
            stmt = stmt.where(Draft.kind == kind)
        return list(s.scalars(stmt))


# ---- reads ------------------------------------------------------------------------------------
async def test_get_card(db):
    async with tools_for(CARDS_MODULE, db) as t:
        card = await t.ok("get_card", card_id=db.ids["saml"])
    assert card["title"] == "Support SAML single sign-on"
    assert card["column"] == "Inbox" and card["column_id"] == db.ids["inbox"]
    assert [tag["name"] for tag in card["tags"]] == ["security"]
    assert (card["vote_count"], card["comment_count"], card["version"]) == (1, 1, 1)
    assert card["requester_type"] == "customer"


async def test_get_unknown_card_is_a_clear_error(db):
    async with tools_for(CARDS_MODULE, db) as t:
        assert "Card 999 not found" in await t.error("get_card", card_id=999)
        assert "card_id" in await t.error("get_card", card_id=0)  # schema: must be >= 1


async def test_list_cards_and_filters(db):
    ids, board = db.ids, db.ids["board"]
    async with tools_for(CARDS_MODULE, db) as t:
        everything = await t.ok("list_cards", board_id=board)
        assert [c["id"] for c in everything["cards"]] == [ids["saml"], ids["okta"], ids["dark"], ids["csv"]]
        assert everything["has_more"] is False

        async def listed(**filters):
            return [c["id"] for c in (await t.ok("list_cards", board_id=board, filters=filters))["cards"]]

        assert await listed(column="inbox") == [ids["saml"], ids["okta"], ids["dark"], ids["csv"]]
        assert await listed(column="Triaged") == []
        assert await listed(column="No such column") == []
        assert await listed(column_id=ids["triaged"]) == []
        assert await listed(tag="SECURITY") == [ids["saml"]]
        assert await listed(tag="nope") == []
        assert await listed(requester_type="partner") == []
        assert await listed(query="saml") == [ids["saml"], ids["okta"]]
        assert await listed(query="100%") == []

        page = await t.ok("list_cards", board_id=board, filters={"limit": 3})
        assert len(page["cards"]) == 3 and page["has_more"] is True
        rest = await t.ok("list_cards", board_id=board, filters={"limit": 3, "offset": 3})
        assert [c["id"] for c in rest["cards"]] == [ids["csv"]] and rest["has_more"] is False

        assert "Board 99 not found" in await t.error("list_cards", board_id=99)
        assert "limit" in await t.error("list_cards", board_id=board, filters={"limit": 0})
        assert "Extra inputs" in await t.error("list_cards", board_id=board, filters={"tagg": "x"})


async def test_get_card_context(db):
    with db.session() as s:
        s.add(CardLink(card_id=db.ids["saml"], other_card_id=db.ids["okta"], kind=LinkKind.duplicate, confidence=0.9))
        s.add(CardProblem(card_id=db.ids["saml"], problem_id=db.ids["problem"], status=LinkStatus.confirmed))
        s.add(Draft(card_id=db.ids["saml"], kind=DraftKind.tag, payload={"name": "x"}))
        s.add(Draft(card_id=db.ids["saml"], kind=DraftKind.tag, payload={"name": "y"}, status=DraftStatus.rejected))
        s.commit()
    async with tools_for(CARDS_MODULE, db) as t:
        context = await t.ok("get_card_context", card_id=db.ids["saml"])
    assert context["card"]["id"] == db.ids["saml"]
    assert [(c["author"], c["body"]) for c in context["comments"]] == [("Alice", "Top blocker in three deals.")]
    assert context["feedback"][0]["quote"] == "We cannot sign without SSO."
    assert context["feedback"][0]["customer_ref"] == "Northwind"
    assert [(link["card_id"], link["kind"], link["confidence"]) for link in context["links"]] == [
        (db.ids["okta"], "duplicate", 0.9)
    ]
    assert context["problems"] == [
        {"id": db.ids["problem"], "title": "Enterprise security gaps", "status": "confirmed", "confidence": None}
    ]
    assert context["pending_suggestions"] == 1  # the rejected one does not count


async def test_list_problems_with_card_counts(db):
    with db.session() as s:
        s.add(CardProblem(card_id=db.ids["saml"], problem_id=db.ids["problem"]))
        s.add(CardProblem(card_id=db.ids["okta"], problem_id=db.ids["problem"]))
        s.commit()
    async with tools_for(CARDS_MODULE, db) as t:
        result = await t.ok("list_problems", board_id=db.ids["board"])
        assert [(p["title"], p["card_count"]) for p in result["problems"]] == [("Enterprise security gaps", 2)]
        assert "Board 99 not found" in await t.error("list_problems", board_id=99)


async def test_list_tags(db):
    async with tools_for(CARDS_MODULE, db) as t:
        result = await t.ok("list_tags", board_id=db.ids["board"])
        assert [(tag["name"], tag["color"]) for tag in result["tags"]] == [("security", "#DC2626"), ("ux", "#DB2777")]
        assert (await t.ok("list_tags", board_id=db.ids["other_board"]))["tags"] == []
        assert "Board 99 not found" in await t.error("list_tags", board_id=99)


# ---- suggestion tools -------------------------------------------------------------------------
async def test_update_card_fields_only_proposes(db):
    card_id = db.ids["saml"]
    async with tools_for(CARDS_MODULE, db) as t:
        result = await t.ok(
            "update_card_fields",
            card_id=card_id,
            fields={"title": "Support SAML 2.0 SSO", "progress": 20},
            rationale="Clearer title.",
            confidence=0.7,
            run_id=db.ids["run"],
        )
        again = await t.ok(
            "update_card_fields",
            card_id=card_id,
            fields={"title": "Support SAML 2.0 SSO", "progress": 20},
            rationale="Clearer title.",
            confidence=0.7,
            run_id=db.ids["run"],
        )
    assert (result["status"], result["applied"], result["created"], result["kind"]) == (
        "pending",
        False,
        True,
        "field_edit",
    )
    assert again["created"] is False and again["draft_id"] == result["draft_id"]  # no duplicate pile-up

    (draft,) = drafts(db, card_id, DraftKind.field_edit)
    assert draft.payload == {
        "fields": {"title": "Support SAML 2.0 SSO", "progress": 20},
        "base_version": 1,
        "rationale": "Clearer title.",
    }
    assert (draft.confidence, draft.run_id, draft.status) == (0.7, db.ids["run"], DraftStatus.pending)
    with db.session() as s:
        card = s.get(Card, card_id)
        assert (card.title, card.progress, card.version) == ("Support SAML single sign-on", 0, 1)


async def test_update_card_fields_validates_input(db):
    async with tools_for(CARDS_MODULE, db) as t:
        ok = {"card_id": db.ids["saml"], "rationale": "r"}
        assert "at least one field" in await t.error("update_card_fields", fields={}, **ok)
        assert "progress" in await t.error("update_card_fields", fields={"progress": 101}, **ok)
        assert "title" in await t.error("update_card_fields", fields={"title": "  "}, **ok)
        assert "Extra inputs" in await t.error("update_card_fields", fields={"votes": 99}, **ok)
        assert "Card 999 not found" in await t.error(
            "update_card_fields", card_id=999, fields={"progress": 1}, rationale="r"
        )
        assert "confidence" in await t.error("update_card_fields", fields={"progress": 1}, confidence=1.5, **ok)
        assert "AI run 4242 not found" in await t.error("update_card_fields", fields={"progress": 1}, run_id=4242, **ok)
    assert drafts(db, db.ids["saml"]) == []


async def test_add_comment_proposes_without_posting(db):
    async with tools_for(CARDS_MODULE, db) as t:
        result = await t.ok("add_comment", card_id=db.ids["okta"], body="  Looks like a duplicate of SAML.  ")
        assert await t.error("add_comment", card_id=db.ids["okta"], body="")
    (draft,) = drafts(db, db.ids["okta"], DraftKind.comment)
    assert draft.payload == {"body": "Looks like a duplicate of SAML."} and result["draft_id"] == draft.id
    with db.session() as s:
        assert s.scalar(select(func.count(Comment.id)).where(Comment.card_id == db.ids["okta"])) == 0


async def test_add_tag_matches_existing_tags_and_proposes_new_ones(db):
    async with tools_for(CARDS_MODULE, db) as t:
        await t.ok("add_tag", card_id=db.ids["okta"], tag="Security", reason="About sign-on.", confidence=0.8)
        await t.ok("add_tag", card_id=db.ids["okta"], tag="sso", reason="New topic.")
        assert "already has the tag 'security'" in await t.error(
            "add_tag", card_id=db.ids["saml"], tag="security", reason="x"
        )
        assert await t.error("add_tag", card_id=db.ids["okta"], tag="x" * 41, reason="x")
    existing, new = drafts(db, db.ids["okta"], DraftKind.tag)
    assert existing.payload == {
        "name": "security",
        "tag_id": db.ids["tag_security"],
        "is_existing": True,
        "reason": "About sign-on.",
    }
    assert new.payload == {"name": "sso", "tag_id": None, "is_existing": False, "reason": "New topic."}
    with db.session() as s:
        assert s.scalar(select(func.count(CardTag.card_id)).where(CardTag.card_id == db.ids["okta"])) == 0


async def test_link_cards_stores_one_suggested_row_per_pair(db):
    ids = db.ids
    async with tools_for(CARDS_MODULE, db) as t:
        first = await t.ok(
            "link_cards",
            card_id=ids["okta"],
            other_card_id=ids["saml"],
            type="related",
            confidence=0.4,
            rationale="Same area.",
        )
        assert first["card_id"] == min(ids["saml"], ids["okta"]) and first["other_card_id"] == max(
            ids["saml"], ids["okta"]
        )
        assert (first["created"], first["status"], first["kind"]) == (True, "suggested", "related")

        again = await t.ok(
            "link_cards",
            card_id=ids["saml"],
            other_card_id=ids["okta"],
            type="duplicate",
            confidence=0.95,
            rationale="Same need.",
        )
        assert again["created"] is False and again["link_id"] == first["link_id"]
        assert (again["kind"], again["confidence"], again["status"]) == ("duplicate", 0.95, "suggested")

        assert "cannot be linked to itself" in await t.error(
            "link_cards",
            card_id=ids["saml"],
            other_card_id=ids["saml"],
            type="duplicate",
            confidence=0.5,
            rationale="x",
        )
        assert "different boards" in await t.error(
            "link_cards",
            card_id=ids["saml"],
            other_card_id=ids["foreign"],
            type="duplicate",
            confidence=0.5,
            rationale="x",
        )
        assert "confidence" in await t.error(
            "link_cards", card_id=ids["saml"], other_card_id=ids["csv"], type="duplicate", confidence=1.2, rationale="x"
        )
        assert "type" in await t.error(
            "link_cards", card_id=ids["saml"], other_card_id=ids["csv"], type="merge", confidence=0.5, rationale="x"
        )
        assert "status" in await t.error(
            "link_cards",
            card_id=ids["saml"],
            other_card_id=ids["csv"],
            type="duplicate",
            confidence=0.5,
            rationale="x",
            status="confirmed",
        )

    with db.session() as s:
        assert s.scalar(select(func.count(CardLink.id))) == 1


async def test_a_confirmed_link_is_never_changed_by_a_suggestion(db):
    ids = db.ids
    with db.session() as s:
        s.add(
            CardLink(
                card_id=ids["saml"],
                other_card_id=ids["okta"],
                kind=LinkKind.duplicate,
                status=LinkStatus.confirmed,
                confidence=0.9,
            )
        )
        s.commit()
    async with tools_for(CARDS_MODULE, db) as t:
        result = await t.ok(
            "link_cards",
            card_id=ids["saml"],
            other_card_id=ids["okta"],
            type="related",
            confidence=0.1,
            rationale="Changed my mind.",
        )
    assert (result["created"], result["status"], result["kind"], result["confidence"]) == (
        False,
        "confirmed",
        "duplicate",
        0.9,
    )


async def test_attach_problem_proposes_membership(db):
    ids = db.ids
    async with tools_for(CARDS_MODULE, db) as t:
        first = await t.ok(
            "attach_problem", card_id=ids["saml"], problem_id=ids["problem"], confidence=0.8, rationale="Security need."
        )
        again = await t.ok(
            "attach_problem", card_id=ids["saml"], problem_id=ids["problem"], confidence=0.6, rationale="Revised."
        )
        assert (first["status"], first["created"]) == ("suggested", True)
        assert (again["created"], again["confidence"]) == (False, 0.6)
        assert "Problem 999 not found" in await t.error(
            "attach_problem", card_id=ids["saml"], problem_id=999, confidence=0.5, rationale="x"
        )
        assert "different board" in await t.error(
            "attach_problem", card_id=ids["saml"], problem_id=ids["other_problem"], confidence=0.5, rationale="x"
        )
        assert "status" in await t.error(
            "attach_problem",
            card_id=ids["saml"],
            problem_id=ids["problem"],
            confidence=0.5,
            rationale="x",
            status="confirmed",
        )
    with db.session() as s:
        row = s.get(CardProblem, (ids["saml"], ids["problem"]))
        assert (row.status, row.confidence, row.rationale) == (LinkStatus.suggested, 0.6, "Revised.")


async def test_save_suggestion_stores_any_kind_as_pending(db):
    card_id = db.ids["saml"]
    async with tools_for(CARDS_MODULE, db) as t:
        for kind in ("tag", "comment", "link", "problem", "score", "brief", "comms", "field_edit"):
            result = await t.ok(
                "save_suggestion", card_id=card_id, kind=kind, payload={"x": kind}, confidence=0.5, run_id=db.ids["run"]
            )
            assert (result["kind"], result["status"], result["applied"], result["created"]) == (
                kind,
                "pending",
                False,
                True,
            )
        assert "payload must not be empty" in await t.error(
            "save_suggestion", card_id=card_id, kind="brief", payload={}
        )
        assert "kind" in await t.error("save_suggestion", card_id=card_id, kind="apply", payload={"a": 1})
        assert "payload is too large" in await t.error(
            "save_suggestion", card_id=card_id, kind="brief", payload={"text": "x" * 25_000}
        )
    stored = drafts(db, card_id)
    assert [d.kind for d in stored] == [
        DraftKind(k) for k in ("tag", "comment", "link", "problem", "score", "brief", "comms", "field_edit")
    ]
    assert all(d.status == DraftStatus.pending and d.run_id == db.ids["run"] for d in stored)


async def test_no_cards_tool_changes_the_card(db):
    """Every write tool together: drafts and suggestions appear, the card and its data stay identical."""
    ids, card_id = db.ids, db.ids["saml"]

    def snapshot():
        with db.session() as s:
            card = s.get(Card, card_id)
            return (
                {c.name: getattr(card, c.name) for c in Card.__table__.columns},
                sorted(t.id for t in card.tags),
                card.comment_count,
                card.vote_count,
            )

    before = snapshot()
    async with tools_for(CARDS_MODULE, db) as t:
        await t.ok(
            "update_card_fields",
            card_id=card_id,
            fields={"title": "New", "priority_score": 90, "progress": 100},
            rationale="r",
        )
        await t.ok("add_comment", card_id=card_id, body="hello")
        await t.ok("add_tag", card_id=card_id, tag="ux", reason="r")
        await t.ok(
            "link_cards", card_id=card_id, other_card_id=ids["okta"], type="duplicate", confidence=0.9, rationale="r"
        )
        await t.ok("attach_problem", card_id=card_id, problem_id=ids["problem"], confidence=0.9, rationale="r")
        await t.ok("save_suggestion", card_id=card_id, kind="brief", payload={"summary": "s"})
    assert snapshot() == before
    assert len(drafts(db, card_id)) == 4


async def test_there_is_no_tool_that_applies_a_suggestion(db):
    async with tools_for(CARDS_MODULE, db) as t:
        names = {tool.name for tool in (await t.session.list_tools()).tools}
    assert not [n for n in names if n.startswith(("apply", "accept", "confirm", "approve"))]
