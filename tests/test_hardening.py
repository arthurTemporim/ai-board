"""Limits and safe handling of awkward input: size caps, validated edits, secrets in logs, lock waits."""

from __future__ import annotations

import logging

import pytest

from apps.api.main import create_app
from core.db import make_engine
from core.models import Draft, DraftKind
from tests.conftest import ALICE
from tests.test_suggestions_api import accept, add_draft


@pytest.fixture
def cards(client, make_card):
    return make_card("Original request"), make_card("Another request")


# ---- free-form inputs are capped --------------------------------------------------------------
def test_oversized_edits_and_decisions_are_rejected_before_anything_is_read(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "ux"})
    huge = {"name": "x" * 30_000}
    response = accept(client, a["id"], sid, edits=huge)
    assert response.status_code == 422 and "smaller than" in str(response.json())

    resume = client.post("/api/workflows/runs/1/resume", json={"decision": {"verdict": "y" * 30_000}})
    assert resume.status_code == 422 and "smaller than" in str(resume.json())


def test_a_field_edit_is_validated_with_the_same_limits_as_the_api(client, session_factory, cards):
    a, _ = cards

    def propose(fields):
        return add_draft(
            session_factory,
            a["id"],
            DraftKind.field_edit,
            {"fields": fields, "base_version": a["version"], "rationale": "r"},
        )

    bad_values = [
        {"title": "x" * 500},
        {"progress": 400},
        {"requester_type": "alien"},
        {"priority_score": -5},
        {"colour": "red"},
    ]
    for fields in bad_values:
        response = accept(client, a["id"], propose(fields))
        assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_fields", fields
    assert client.get(f"/api/cards/{a['id']}").json()["title"] == "Original request"  # nothing leaked through

    # a person's edit is held to the same rules
    sid = propose({"title": "Fine title"})
    assert accept(client, a["id"], sid, edits={"progress": "lots"}).json()["error"]["code"] == "invalid_fields"
    assert accept(client, a["id"], sid, edits={"progress": 40}).status_code == 200
    card = client.get(f"/api/cards/{a['id']}").json()
    assert (card["title"], card["progress"]) == ("Fine title", 40)


def test_message_and_problem_edits_are_clamped_and_empty_ones_refused(client, session_factory, cards):
    a, _ = cards
    comms = add_draft(
        session_factory,
        a["id"],
        DraftKind.comms,
        {"audience": "requester", "subject": "Hi", "body": "Text", "tone": "neutral"},
    )
    assert accept(client, a["id"], comms, edits={"subject": "  "}).json()["error"]["code"] == "invalid_draft"
    assert accept(client, a["id"], comms, edits={"subject": "s" * 400, "body": "b" * 5000}).status_code == 200
    with session_factory() as s:
        draft = s.get(Draft, int(comms.split(":")[1]))
        assert (len(draft.payload["subject"]), len(draft.payload["body"])) == (120, 2000)

    problem = add_draft(
        session_factory, a["id"], DraftKind.problem, {"new_problem_title": "T", "new_problem_statement": "S"}
    )
    assert accept(client, a["id"], problem, edits={"statement": "z" * 9000}).status_code == 200
    assert len(client.get(f"/api/cards/{a['id']}").json()["problems"]) == 1


def test_a_brief_comment_cannot_exceed_the_comment_limit(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(session_factory, a["id"], DraftKind.brief, {"summary": "s", "recommendation": "plan"})
    assert accept(client, a["id"], sid, edits={"summary": "long " * 1600}).status_code == 200
    (comment,) = client.get(f"/api/cards/{a['id']}/comments").json()
    assert len(comment["body"]) <= 5000


# ---- secrets never reach the logs -------------------------------------------------------------
def test_an_unexpected_error_is_logged_without_secrets_and_answered_without_details(
    session_factory, monkeypatch, caplog
):
    monkeypatch.setenv("LITELLM_API_KEY", "sk-live-TOPSECRET987654")
    app = create_app()

    @app.get("/boom")
    def boom():
        raise RuntimeError("upstream said: Authorization: Bearer sk-live-TOPSECRET987654")

    from fastapi.testclient import TestClient

    with caplog.at_level(logging.ERROR), TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "Internal server error", "details": []}}
    assert "TOPSECRET" not in caplog.text and "TOPSECRET" not in response.text
    assert "RuntimeError" in caplog.text and "[redacted]" in caplog.text  # still useful for debugging


# ---- the database waits for locks -------------------------------------------------------------
def test_file_databases_wait_for_a_lock_instead_of_failing_at_once(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'x.db'}")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_nothing_in_the_api_surface_returns_environment_values(client, monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "sk-live-TOPSECRET987654")
    for path in ("/health", "/openapi.json", "/api/users", "/api/metrics"):
        assert "TOPSECRET" not in client.get(path).text


def test_requests_without_a_user_cannot_decide_anything(client, session_factory, cards):
    a, _ = cards
    sid = add_draft(session_factory, a["id"], DraftKind.tag, {"name": "x"})
    assert client.post(f"/api/cards/{a['id']}/suggestions/{sid}/accept", json={}).status_code == 401
    assert client.post(f"/api/cards/{a['id']}/suggestions/{sid}/accept", json={}, headers=ALICE).status_code == 200
