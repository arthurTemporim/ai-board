from __future__ import annotations

import pytest
from sqlalchemy import select

from core.models import Draft, DraftKind, DraftStatus
from tests.mcp.conftest import COMMS_MODULE, tools_for

pytestmark = pytest.mark.anyio

DRAFT = {"audience": "requester", "subject": "Your request", "body": "It is under review.", "tone": "neutral"}


async def test_save_draft_creates_a_pending_comms_draft(db):
    async with tools_for(COMMS_MODULE, db) as t:
        result = await t.ok("save_draft", card_id=db.ids["saml"], confidence=0.6, run_id=db.ids["run"], **DRAFT)
    assert (result["kind"], result["status"], result["applied"], result["created"]) == ("comms", "pending", False, True)
    with db.session() as s:
        (draft,) = s.scalars(select(Draft)).all()
        assert draft.id == result["draft_id"] and draft.card_id == db.ids["saml"]
        assert draft.kind == DraftKind.comms and draft.status == DraftStatus.pending
        assert draft.payload == DRAFT
        assert (draft.confidence, draft.run_id) == (0.6, db.ids["run"])


async def test_save_draft_defaults_tone_trims_text_and_does_not_duplicate(db):
    args = {"card_id": db.ids["saml"], "audience": "internal", "subject": "  Brief  ", "body": "  Text.  "}
    async with tools_for(COMMS_MODULE, db) as t:
        first = await t.ok("save_draft", **args)
        second = await t.ok("save_draft", **args)
    assert first["created"] is True and second["created"] is False and second["draft_id"] == first["draft_id"]
    with db.session() as s:
        (draft,) = s.scalars(select(Draft)).all()
        assert draft.payload == {"audience": "internal", "subject": "Brief", "body": "Text.", "tone": "neutral"}


@pytest.mark.parametrize(
    "change, message",
    [
        ({"audience": "press"}, "audience"),
        ({"tone": "furious"}, "tone"),
        ({"subject": ""}, "subject"),
        ({"body": ""}, "body"),
        ({"body": "x" * 2001}, "body"),
        ({"confidence": 2}, "confidence"),
        ({"card_id": 999}, "Card 999 not found"),
        ({"run_id": 555}, "AI run 555 not found"),
    ],
)
async def test_save_draft_validates_input(db, change, message):
    args = {"card_id": db.ids["saml"], **DRAFT, **change}
    async with tools_for(COMMS_MODULE, db) as t:
        assert message in await t.error("save_draft", **args)
    with db.session() as s:
        assert s.scalars(select(Draft)).all() == []


async def test_list_drafts_defaults_to_comms_and_filters(db):
    card_id = db.ids["saml"]
    with db.session() as s:
        s.add(Draft(card_id=card_id, kind=DraftKind.tag, payload={"name": "ux"}))
        s.add(Draft(card_id=db.ids["okta"], kind=DraftKind.comms, payload=DRAFT))
        s.commit()
    async with tools_for(COMMS_MODULE, db) as t:
        first = await t.ok("save_draft", card_id=card_id, **DRAFT)
        second = await t.ok("save_draft", card_id=card_id, **{**DRAFT, "subject": "Second"})
        with db.session() as s:
            s.get(Draft, first["draft_id"]).status = DraftStatus.rejected
            s.commit()

        comms = await t.ok("list_drafts", card_id=card_id)
        assert [d["id"] for d in comms["drafts"]] == [second["draft_id"], first["draft_id"]]  # newest first
        assert all(d["kind"] == "comms" for d in comms["drafts"])
        assert comms["drafts"][0]["payload"]["subject"] == "Second"

        pending = await t.ok("list_drafts", card_id=card_id, status="pending")
        assert [d["id"] for d in pending["drafts"]] == [second["draft_id"]]
        everything = await t.ok("list_drafts", card_id=card_id, kind=None)
        assert {d["kind"] for d in everything["drafts"]} == {"comms", "tag"}
        only_tags = await t.ok("list_drafts", card_id=card_id, kind="tag")
        assert [d["payload"] for d in only_tags["drafts"]] == [{"name": "ux"}]
        assert len((await t.ok("list_drafts", card_id=card_id, limit=1))["drafts"]) == 1
        assert (await t.ok("list_drafts", card_id=db.ids["dark"]))["drafts"] == []
        assert "Card 999 not found" in await t.error("list_drafts", card_id=999)
        assert "limit" in await t.error("list_drafts", card_id=card_id, limit=0)


async def test_get_templates_returns_the_config_with_placeholders(db):
    async with tools_for(COMMS_MODULE, db) as t:
        all_templates = (await t.ok("get_templates"))["templates"]
        requester = (await t.ok("get_templates", audience="requester"))["templates"]
        sales = (await t.ok("get_templates", audience="sales"))["templates"]
        assert "audience" in await t.error("get_templates", audience="press")
    names = {t["name"] for t in all_templates}
    assert {
        "request_received",
        "under_review",
        "planned",
        "needs_info",
        "declined",
        "internal_summary",
        "sales_heads_up",
    } <= names
    assert {t["audience"] for t in requester} == {"requester"} and [t["name"] for t in sales] == ["sales_heads_up"]
    received = next(t for t in all_templates if t["name"] == "request_received")
    assert received["placeholders"] == ["requester_name", "title"]
    assert "{title}" in received["subject"] and received["description"]


async def test_templates_can_come_from_another_file_and_bad_files_are_reported(db, tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text(
        "templates:\n"
        "  - {name: hello, audience: internal, description: d, subject: 'Hi {who}', body: 'Body {what} {who}'}\n",
        encoding="utf-8",
    )
    db.env["COMMS_TEMPLATES_PATH"] = str(path)
    async with tools_for(COMMS_MODULE, db) as t:
        (template,) = (await t.ok("get_templates"))["templates"]
    assert (template["name"], template["placeholders"]) == ("hello", ["what", "who"])

    path.write_text("templates:\n  - {name: broken, audience: nobody}\n", encoding="utf-8")
    async with tools_for(COMMS_MODULE, db) as t:
        assert "Cannot load comms templates" in await t.error("get_templates")
    db.env["COMMS_TEMPLATES_PATH"] = str(tmp_path / "absent.yaml")
    async with tools_for(COMMS_MODULE, db) as t:
        assert "not found" in await t.error("get_templates")
