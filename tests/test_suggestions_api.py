"""Accepting and rejecting AI suggestions: what each type applies, validation, audit entries."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from core.models import (
    AuditEntry,
    Card,
    CardLink,
    CardProblem,
    Draft,
    DraftKind,
    DraftStatus,
    LinkKind,
    LinkStatus,
    Problem,
    Score,
    Tag,
    Vote,
)
from tests.conftest import ALICE, BOB

RATINGS = {"customer_impact": 4, "demand_breadth": 3, "strategic_fit": 2, "effort_fit": 5}


def url(card_id: int, sid: str, verb: str) -> str:
    return f"/api/cards/{card_id}/suggestions/{sid}/{verb}"


def accept(client, card_id, sid, headers=ALICE, **body):
    return client.post(url(card_id, sid, "accept"), json=body, headers=headers)


def reject(client, card_id, sid, headers=ALICE, **body):
    return client.post(url(card_id, sid, "reject"), json=body, headers=headers)


def add_draft(session_factory, card_id, kind, payload, confidence=0.8, **kw) -> str:
    with session_factory() as s:
        d = Draft(card_id=card_id, kind=kind, payload=payload, confidence=confidence, **kw)
        s.add(d)
        s.commit()
        return f"draft:{d.id}"


def add_link(session_factory, a, b, kind=LinkKind.duplicate, confidence=0.9, rationale="Same need.") -> str:
    low, high = sorted((a, b))
    with session_factory() as s:
        link = CardLink(card_id=low, other_card_id=high, kind=kind, confidence=confidence, rationale=rationale)
        s.add(link)
        s.commit()
        return f"link:{link.id}"


def add_score(session_factory, card_id, ratings=RATINGS) -> str:
    from core.services.scoring import CriterionScore, ScoringService

    with session_factory() as s:
        row, priority = ScoringService(s).save_scores(
            card_id, [CriterionScore(key=k, rating=v, rationale=f"why {k}") for k, v in ratings.items()]
        )
        s.commit()
        payload = {
            "score_id": row.id,
            "total": priority.total,
            "rubric": "default v1",
            "lines": [line.model_dump() for line in priority.lines],
        }
    return add_draft(session_factory, card_id, DraftKind.score, payload)


def audit(session_factory, card_id=None):
    with session_factory() as s:
        stmt = select(AuditEntry).order_by(AuditEntry.id)
        if card_id:
            stmt = stmt.where(AuditEntry.card_id == card_id)
        return list(s.scalars(stmt))


@pytest.fixture
def cards(client, make_card):
    return make_card("Original request"), make_card("Same request, other words")


# ---- listing ----------------------------------------------------------------------------------
def test_listing_shows_every_kind_with_confidence_and_reasoning(client, session_factory, cards, board):
    a, b = cards
    add_link(session_factory, a["id"], b["id"], rationale="Both want the same thing.")
    add_draft(session_factory, b["id"], DraftKind.tag, {"name": "ux", "reason": "About the UI."})
    add_draft(
        session_factory,
        b["id"],
        DraftKind.brief,
        {"summary": "Do it.", "recommendation": "plan", "risks": [], "open_questions": []},
    )
    with session_factory() as s:
        problem = Problem(board_id=board["id"], title="Slow reporting", statement="")
        s.add(problem)
        s.flush()
        s.add(CardProblem(card_id=b["id"], problem_id=problem.id, confidence=0.7, rationale="Matches."))
        s.commit()
    add_score(session_factory, b["id"])

    listed = client.get(f"/api/cards/{b['id']}/suggestions").json()
    by_type = {s["type"]: s for s in listed}
    assert list(by_type) == ["duplicate", "problem", "tag", "score", "brief"]  # a stable order
    dup = by_type["duplicate"]
    assert (dup["confidence"], dup["rationale"], dup["status"]) == (0.9, "Both want the same thing.", "suggested")
    assert dup["data"]["other_card_id"] == a["id"] and dup["data"]["merge"]["primary_card_id"] == a["id"]
    assert (
        by_type["problem"]["data"]["problem_title"] == "Slow reporting"
        and by_type["problem"]["rationale"] == "Matches."
    )
    assert by_type["tag"]["rationale"] == "About the UI." and by_type["tag"]["editable"] == ["name"]
    score = by_type["score"]["data"]
    assert [line["key"] for line in score["lines"]] == list(RATINGS) and score["lines"][0][
        "rationale"
    ] == "why customer_impact"
    assert by_type["brief"]["editable"] == ["summary"]
    # the other end of the link sees the same duplicate
    assert [s["type"] for s in client.get(f"/api/cards/{a['id']}/suggestions").json()] == ["duplicate"]


def test_decided_suggestions_are_hidden_unless_asked_for(client, session_factory, cards):
    a, b = cards
    sid = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "ux"})
    assert accept(client, a["id"], sid).status_code == 200
    assert client.get(f"/api/cards/{a['id']}/suggestions").json() == []
    shown = client.get(f"/api/cards/{a['id']}/suggestions", params={"include_decided": True}).json()
    assert [(s["id"], s["status"]) for s in shown] == [(sid, "applied")]


def test_needs_review_and_pending_counts_follow_the_suggestions(client, session_factory, cards, board):
    a, b = cards
    detail = lambda cid: client.get(f"/api/cards/{cid}").json()  # noqa: E731
    assert (detail(a["id"])["needs_review"], detail(a["id"])["pending_suggestion_count"]) == (False, 0)

    link = add_link(session_factory, a["id"], b["id"])
    # a merge proposal draft belongs to the link and is not counted a second time
    add_draft(
        session_factory, b["id"], DraftKind.link, {"action": "merge", "keep_card_id": a["id"], "merge_card_id": b["id"]}
    )
    tag = add_draft(session_factory, b["id"], DraftKind.tag, {"name": "ux"})
    assert (detail(b["id"])["pending_suggestion_count"], detail(b["id"])["needs_review"]) == (2, True)
    assert detail(a["id"])["pending_suggestion_count"] == 1

    accept(client, b["id"], tag)
    reject(client, b["id"], link)
    assert (detail(b["id"])["pending_suggestion_count"], detail(b["id"])["needs_review"]) == (0, False)
    # a rejected link also stops counting as a duplicate on the card
    assert detail(b["id"])["duplicate_count"] == 0

    on_board = client.get(f"/api/boards/{board['id']}").json()
    assert all(c["needs_review"] is False for col in on_board["columns"] for c in col["cards"])


# ---- duplicates: merge votes ------------------------------------------------------------------
def test_accepting_a_duplicate_moves_votes_to_the_primary_and_keeps_the_link(client, session_factory, cards):
    a, b = cards
    client.post(f"/api/cards/{a['id']}/votes", headers=ALICE)
    client.post(f"/api/cards/{b['id']}/votes", headers=ALICE)  # the same person voted for both
    client.post(f"/api/cards/{b['id']}/votes", headers=BOB)
    sid = add_link(session_factory, a["id"], b["id"])
    merge_draft = add_draft(
        session_factory, b["id"], DraftKind.link, {"action": "merge", "keep_card_id": a["id"], "merge_card_id": b["id"]}
    )

    result = accept(client, b["id"], sid)
    assert result.status_code == 200
    body = result.json()
    assert (body["status"], body["effects"]["votes_moved"], body["effects"]["votes_already_on_primary"]) == (
        "confirmed",
        1,
        1,
    )
    assert "1 vote(s) moved to card" in body["message"]

    assert client.get(f"/api/cards/{a['id']}").json()["vote_count"] == 2  # Alice once, plus Bob
    assert client.get(f"/api/cards/{b['id']}").json()["vote_count"] == 0
    with session_factory() as s:
        assert sorted(v.user_id for v in s.scalars(select(Vote).where(Vote.card_id == a["id"]))) == [1, 2]
        link = s.scalars(select(CardLink)).one()
        assert link.status is LinkStatus.confirmed and link.kind is LinkKind.duplicate
        assert s.get(Draft, int(merge_draft.split(":")[1])).status is DraftStatus.applied
    kinds = [(e.action, e.card_id, e.actor_id) for e in audit(session_factory)]
    assert kinds == [("duplicate_merged", b["id"], 1), ("suggestion_accepted", b["id"], 1)]
    merged = audit(session_factory)[0].detail
    assert (
        merged["primary_card_id"],
        merged["duplicate_card_id"],
        merged["moved_user_ids"],
        merged["dropped_user_ids"],
    ) == (a["id"], b["id"], [2], [1])
    # the original stays linked from both sides
    assert client.get(f"/api/cards/{a['id']}").json()["links"][0]["status"] == "confirmed"


def test_the_merge_proposals_choice_of_primary_is_respected(client, session_factory, cards):
    a, b = cards
    client.post(f"/api/cards/{a['id']}/votes", headers=BOB)
    sid = add_link(session_factory, a["id"], b["id"])
    add_draft(
        session_factory, a["id"], DraftKind.link, {"action": "merge", "keep_card_id": b["id"], "merge_card_id": a["id"]}
    )
    listed = client.get(f"/api/cards/{a['id']}/suggestions").json()[0]
    assert listed["data"]["merge"] == {
        "primary_card_id": b["id"],
        "duplicate_card_id": a["id"],
        "votes_on_duplicate": 1,
    }
    assert accept(client, a["id"], sid).json()["effects"]["primary_card_id"] == b["id"]
    assert client.get(f"/api/cards/{b['id']}").json()["vote_count"] == 1


def test_accepting_a_related_link_only_confirms_it(client, session_factory, cards):
    a, b = cards
    client.post(f"/api/cards/{b['id']}/votes", headers=BOB)
    sid = add_link(session_factory, a["id"], b["id"], kind=LinkKind.related)
    result = accept(client, a["id"], sid).json()
    assert (result["status"], result["effects"]) == ("confirmed", {})
    assert client.get(f"/api/cards/{b['id']}").json()["vote_count"] == 1  # nothing moved
    assert [e.action for e in audit(session_factory)] == ["suggestion_accepted"]


def test_rejecting_a_link_is_remembered_and_audited_with_the_reason(client, session_factory, cards):
    a, b = cards
    sid = add_link(session_factory, a["id"], b["id"])
    merge = add_draft(
        session_factory, b["id"], DraftKind.link, {"action": "merge", "keep_card_id": a["id"], "merge_card_id": b["id"]}
    )
    result = reject(client, b["id"], sid, reason="Different customers")
    assert result.status_code == 200 and result.json()["status"] == "rejected"
    with session_factory() as s:
        assert s.scalars(select(CardLink)).one().status is LinkStatus.rejected
        assert s.get(Draft, int(merge.split(":")[1])).status is DraftStatus.rejected
    (entry,) = audit(session_factory)
    assert (
        entry.action == "suggestion_rejected"
        and entry.detail["reason"] == "Different customers"
        and entry.detail["type"] == "duplicate"
    )
    # the AI can no longer re-propose or refresh it
    from core.services.suggestions import SuggestionService

    with session_factory() as s:
        link, created = SuggestionService(s).suggest_link(
            a["id"], b["id"], LinkKind.duplicate, confidence=0.99, rationale="again"
        )
        assert (created, link.status, link.confidence) == (False, LinkStatus.rejected, 0.9)
    assert client.get(f"/api/cards/{a['id']}").json()["links"] == [] or all(
        link["status"] != "suggested" for link in client.get(f"/api/cards/{a['id']}").json()["links"]
    )


# ---- tags, problems ---------------------------------------------------------------------------
def test_accepting_a_tag_adds_it_creating_it_if_needed(client, session_factory, cards, board):
    a, _ = cards
    existing = client.post("/api/tags", json={"board_id": board["id"], "name": "ux", "color": "#DB2777"}).json()
    s1 = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "UX", "tag_id": existing["id"]})
    s2 = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "sso"})
    assert accept(client, a["id"], s1).json()["effects"] == {"tag_id": existing["id"], "tag_created": False}
    assert accept(client, a["id"], s2).json()["effects"]["tag_created"] is True
    card = client.get(f"/api/cards/{a['id']}").json()
    assert [t["name"] for t in card["tags"]] == ["sso", "ux"] and card["version"] == a["version"] + 2
    with session_factory() as s:
        assert s.scalars(select(Tag).where(Tag.name == "sso")).one().color == "#6B7280"


def test_a_tag_can_be_edited_before_accepting_and_edits_are_validated(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "sso"})
    assert accept(client, a["id"], sid, edits={"colour": "red"}).status_code == 422
    assert accept(client, a["id"], sid, edits={"name": ""}).json()["error"]["code"] == "invalid_tag"
    assert accept(client, a["id"], sid, edits={"name": "single-sign-on"}).status_code == 200
    assert [t["name"] for t in client.get(f"/api/cards/{a['id']}").json()["tags"]] == ["single-sign-on"]
    assert audit(session_factory)[-1].detail["edited"] is True


def test_problem_suggestions(client, session_factory, cards, board):
    a, b = cards
    with session_factory() as s:
        p = Problem(board_id=board["id"], title="Slow reporting", statement="")
        s.add(p)
        s.flush()
        s.add_all(
            [
                CardProblem(card_id=a["id"], problem_id=p.id, confidence=0.7),
                CardProblem(card_id=b["id"], problem_id=p.id, confidence=0.6),
            ]
        )
        s.commit()
        pid = p.id
    assert accept(client, a["id"], f"problem:{a['id']}:{pid}").json()["effects"] == {"problem_id": pid}
    assert reject(client, b["id"], f"problem:{b['id']}:{pid}").status_code == 200
    with session_factory() as s:
        assert s.get(CardProblem, (a["id"], pid)).status is LinkStatus.confirmed
        assert s.get(CardProblem, (b["id"], pid)).status is LinkStatus.rejected
    assert client.get(f"/api/cards/{a['id']}").json()["problems"][0]["status"] == "confirmed"


def test_a_proposed_new_problem_is_created_when_accepted(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(
        session_factory,
        a["id"],
        DraftKind.problem,
        {"new_problem_title": "Login pain", "new_problem_statement": "Staff juggle passwords."},
    )
    listed = client.get(f"/api/cards/{a['id']}/suggestions").json()[0]
    assert listed["type"] == "new_problem" and listed["data"] == {
        "title": "Login pain",
        "statement": "Staff juggle passwords.",
    }
    assert accept(client, a["id"], sid, edits={"title": "Password overload"}).status_code == 200
    problems = client.get(f"/api/cards/{a['id']}").json()["problems"]
    assert [(p["title"], p["status"]) for p in problems] == [("Password overload", "confirmed")]


# ---- scores -----------------------------------------------------------------------------------
def test_accepting_a_score_sets_the_cards_priority(client, session_factory, cards):
    a, _ = cards
    sid = add_score(session_factory, a["id"])
    result = accept(client, a["id"], sid).json()
    assert result["status"] == "applied" and result["effects"]["priority_score"] == 59.38
    assert client.get(f"/api/cards/{a['id']}").json()["priority_score"] == 59.38
    assert audit(session_factory)[0].detail["after"] == 59.38


def test_overriding_a_score_needs_a_reason_and_records_both_values(client, session_factory, cards):
    a, _ = cards
    sid = add_score(session_factory, a["id"])
    no_reason = accept(client, a["id"], sid, override={"ratings": {"strategic_fit": 5}})
    assert no_reason.status_code == 422
    short = accept(client, a["id"], sid, override={"ratings": {"strategic_fit": 5}, "reason": "no"})
    assert short.status_code == 422
    assert client.get(f"/api/cards/{a['id']}").json()["priority_score"] is None  # nothing applied by the failed tries

    ok = accept(
        client,
        a["id"],
        sid,
        override={"ratings": {"strategic_fit": 5, "effort_fit": 1}, "reason": "Strategy changed this quarter"},
    )
    assert ok.status_code == 200
    assert ok.json()["effects"]["overridden"] == {"strategic_fit": [2, 5], "effort_fit": [5, 1]}
    expected = 65.62  # ratings 4, 3, 5, 1 on a 1-5 scale with weights 3, 2, 2, 1: 5.25 / 8 of the scale
    assert client.get(f"/api/cards/{a['id']}").json()["priority_score"] == pytest.approx(expected, abs=0.01)
    (entry,) = [e for e in audit(session_factory) if e.action == "score_override"]
    assert entry.detail["reason"] == "Strategy changed this quarter" and entry.detail["ai_total"] == 59.38
    assert entry.detail["changes"] == {"strategic_fit": [2, 5], "effort_fit": [5, 1]}
    with session_factory() as s:
        latest = s.scalars(select(Score).order_by(Score.id.desc())).first()
        assert latest.per_criterion["strategic_fit"]["rationale"].startswith("Overridden: Strategy changed")
        assert latest.per_criterion["customer_impact"]["rationale"] == "why customer_impact"


@pytest.mark.parametrize(
    "override, message",
    [
        ({"ratings": {"charisma": 3}, "reason": "valid reason"}, "Unknown criteria"),
        ({"ratings": {"effort_fit": 9}, "reason": "valid reason"}, "outside 1-5"),
        ({"ratings": {"effort_fit": 5}, "reason": "valid reason"}, "does not change any rating"),
        ({"ratings": {}, "reason": "valid reason"}, "ratings"),
    ],
)
def test_bad_overrides_are_rejected(client, session_factory, cards, override, message):
    a, _ = cards
    sid = add_score(session_factory, a["id"])
    response = accept(client, a["id"], sid, override=override)
    assert response.status_code == 422 and message in str(response.json())
    assert client.get(f"/api/cards/{a['id']}").json()["priority_score"] is None
    with session_factory() as s:
        assert s.get(Draft, int(sid.split(":")[1])).status is DraftStatus.pending


def test_only_scores_can_be_overridden(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "x"})
    response = accept(client, a["id"], sid, override={"ratings": {"a": 1}, "reason": "valid reason"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "not_overridable"


# ---- brief, comments, comms, field edits ------------------------------------------------------
def test_accepting_a_brief_posts_it_as_a_comment_by_the_person_accepting(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(
        session_factory,
        a["id"],
        DraftKind.brief,
        {"summary": "Worth doing.", "recommendation": "plan", "risks": ["Scope"], "open_questions": ["Who owns it?"]},
    )
    assert accept(client, a["id"], sid, headers=BOB, edits={"summary": "Worth doing soon."}).status_code == 200
    (comment,) = client.get(f"/api/cards/{a['id']}/comments").json()
    assert (
        comment["author"]["name"] == "Bob"
        and "Worth doing soon." in comment["body"]
        and "Recommendation: plan" in comment["body"]
    )
    assert "Risks: Scope" in comment["body"] and "Open questions: Who owns it?" in comment["body"]
    assert audit(session_factory)[0].actor_id == 2


def test_accepting_a_comment_suggestion_posts_it(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(session_factory, a["id"], DraftKind.comment, {"body": "Needs more detail."})
    accept(client, a["id"], sid)
    assert client.get(f"/api/cards/{a['id']}").json()["comment_count"] == 1


def test_accepting_a_message_draft_approves_it_without_sending_anything(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(
        session_factory,
        a["id"],
        DraftKind.comms,
        {"audience": "requester", "subject": "Hi", "body": "Under review.", "tone": "neutral"},
    )
    result = accept(client, a["id"], sid, edits={"body": "Under review, thanks!"}).json()
    assert result["status"] == "accepted" and "Nothing was sent" in result["message"]
    with session_factory() as s:
        draft = s.get(Draft, int(sid.split(":")[1]))
        assert (
            draft.status is DraftStatus.accepted
            and draft.payload["body"] == "Under review, thanks!"
            and draft.decided_by_id == 1
        )


def test_an_analysis_can_be_acknowledged(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(session_factory, a["id"], DraftKind.analysis, {"underlying_need": "x", "rationale": "because"})
    assert accept(client, a["id"], sid).json()["status"] == "accepted"
    assert accept(client, a["id"], sid, edits={"x": 1}).status_code == 409  # already decided wins over bad edits


def test_a_field_edit_is_applied_unless_the_card_changed_meanwhile(client, session_factory, cards):
    a, b = cards
    fresh = add_draft(
        session_factory,
        a["id"],
        DraftKind.field_edit,
        {"fields": {"title": "Better title", "progress": 20}, "base_version": a["version"], "rationale": "r"},
    )
    stale = add_draft(
        session_factory,
        b["id"],
        DraftKind.field_edit,
        {"fields": {"title": "X"}, "base_version": b["version"], "rationale": "r"},
    )
    client.patch(f"/api/cards/{b['id']}", json={"title": "Edited by a person"})
    shown = client.get(f"/api/cards/{b['id']}/suggestions").json()[0]
    assert shown["data"]["stale"] is True

    assert accept(client, a["id"], fresh).status_code == 200
    card = client.get(f"/api/cards/{a['id']}").json()
    assert (card["title"], card["progress"], card["version"]) == ("Better title", 20, a["version"] + 1)
    conflict = accept(client, b["id"], stale)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "version_conflict"
    assert client.get(f"/api/cards/{b['id']}").json()["title"] == "Edited by a person"


# ---- rules that apply to every decision -------------------------------------------------------
def test_a_decision_needs_a_known_user(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "x"})
    assert accept(client, a["id"], sid, headers={}).json()["error"]["code"] == "actor_required"
    assert reject(client, a["id"], sid, headers={"X-User-Id": "99"}).json()["error"]["code"] == "unknown_actor"
    assert audit(session_factory) == []


def test_a_suggestion_can_only_be_decided_once_and_only_on_its_own_card(client, session_factory, cards):
    a, b = cards
    sid = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "x"})
    assert accept(client, b["id"], sid).status_code == 404  # belongs to card a
    assert accept(client, a["id"], sid).status_code == 200
    again = accept(client, a["id"], sid)
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_decided"
    assert reject(client, a["id"], sid).status_code == 409
    assert len(audit(session_factory)) == 1


@pytest.mark.parametrize("sid", ["nonsense", "draft:x", "draft:1:2", "link:", "problem:1", "other:1"])
def test_malformed_ids_are_rejected(client, cards, sid):
    a, _ = cards
    assert accept(client, a["id"], sid).json()["error"]["code"] == "bad_suggestion_id"


def test_unknown_things_are_404(client, cards):
    a, _ = cards
    assert accept(client, a["id"], "draft:999").status_code == 404
    assert accept(client, 999, "draft:1").status_code == 404
    assert reject(client, a["id"], "link:999").status_code == 404
    assert client.get("/api/cards/999/suggestions").status_code == 404


def test_merge_proposals_cannot_be_decided_directly(client, session_factory, cards):
    a, b = cards
    sid = add_draft(
        session_factory, b["id"], DraftKind.link, {"action": "merge", "keep_card_id": a["id"], "merge_card_id": b["id"]}
    )
    assert accept(client, b["id"], sid).json()["error"]["code"] == "use_link"
    assert reject(client, b["id"], sid).json()["error"]["code"] == "use_link"


def test_edits_on_a_link_are_not_allowed(client, session_factory, cards):
    a, b = cards
    sid = add_link(session_factory, a["id"], b["id"])
    assert accept(client, a["id"], sid, edits={"x": 1}).json()["error"]["code"] == "not_editable"


def test_failed_decisions_leave_no_trace(client, session_factory, cards):
    a, b = cards
    sid = add_link(session_factory, a["id"], b["id"], kind=LinkKind.related)
    accept(client, a["id"], sid, edits={"x": 1})
    with session_factory() as s:
        assert s.scalars(select(CardLink)).one().status is LinkStatus.suggested
        assert s.scalars(select(AuditEntry)).all() == []


def test_the_audit_endpoint_lists_decisions_newest_first(client, session_factory, cards):
    a, _ = cards
    t1 = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "one"})
    t2 = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "two"})
    accept(client, a["id"], t1)
    reject(client, a["id"], t2, reason="Not relevant")
    entries = client.get(f"/api/cards/{a['id']}/audit").json()
    assert [(e["action"], e["detail"]["suggestion"]) for e in entries] == [
        ("suggestion_rejected", t2),
        ("suggestion_accepted", t1),
    ]
    assert entries[0]["actor_id"] == 1 and entries[0]["created_at"].endswith("Z")
    assert client.get("/api/cards/999/audit").status_code == 404


def test_deleting_a_card_keeps_its_audit_entries(client, session_factory, cards):
    a, _ = cards
    accept(client, a["id"], add_draft(session_factory, a["id"], DraftKind.tag, {"name": "x"}))
    client.delete(f"/api/cards/{a['id']}")
    entries = audit(session_factory)
    assert len(entries) == 1 and entries[0].card_id is None  # the record survives, detached from the deleted card
    with session_factory() as s:
        assert s.get(Card, a["id"]) is None
