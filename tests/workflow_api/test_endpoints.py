from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import select

from ai.graph.state import STEPS, GraphConfig
from core.models import AIRun, RunStatus
from tests.graph.conftest import DISTINCT, DUPLICATE_OF_SAML, TEST_CONFIG, ScriptedLLM, add_card
from tests.workflow_api.conftest import ALICE, BOB

UNSURE = GraphConfig(**{**TEST_CONFIG.__dict__, "merge_threshold": 0.95})


# ---- triage -----------------------------------------------------------------------------------
def test_triage_an_existing_card_runs_in_the_background_and_reports_progress(make_api, db):
    api = make_api()
    card_id = add_card(db, *DISTINCT)
    accepted = api.triage(card_id=card_id)

    assert accepted["card_id"] == card_id and accepted["card_created"] is False
    assert accepted["status"] == "running" and accepted["steps_requested"] is None
    run = api.wait(accepted["run_id"])

    assert (run["status"], run["outcome"]) == ("succeeded", "completed")
    assert [s["name"] for s in run["steps"]][:8] == list(STEPS)
    assert all(s["status"] == "succeeded" for s in run["steps"])
    assert run["progress"] == {"done": 8, "total": 8}
    assert run["pending_action"] is None and run["errors"] == []
    score = next(s for s in run["steps"] if s["name"] == "score")
    assert 0 <= score["result"]["total"] <= 100 and score["latency_ms"] is not None


def test_triage_with_a_card_payload_creates_the_card_first(make_api, db):
    api = make_api()
    body = {
        "board_id": db.ids["board"],
        "title": "Offline mode for field technicians",
        "description": "Technicians work in basements without signal.",
        "requester_type": "partner",
    }
    accepted = api.triage(card=body)
    assert accepted["card_created"] is True
    card = api.client.get(f"/api/cards/{accepted['card_id']}").json()
    assert (card["title"], card["requester_type"], card["created_by_id"]) == (body["title"], "partner", 1)
    assert card["column_id"] == db.ids["inbox"]
    assert api.wait(accepted["run_id"])["status"] == "succeeded"


def test_partial_runs_only_run_the_requested_steps(make_api, db):
    api = make_api()
    card_id = add_card(db, *DISTINCT)
    run = api.wait(api.triage(card_id=card_id, steps=["score"])["run_id"])
    assert {s["name"]: s["status"] for s in run["steps"][:8]} == {
        step: ("succeeded" if step == "score" else "skipped") for step in STEPS
    }
    assert run["progress"] == {"done": 1, "total": 1}
    skipped = next(s for s in run["steps"] if s["name"] == "find_related")
    assert skipped["reason"] == "not requested" and skipped["requested"] is False


def test_the_single_step_endpoint_runs_one_step(make_api, db):
    api = make_api()
    card_id = add_card(db, *DISTINCT)
    response = api.client.post("/api/workflows/steps/analyze", json={"card_id": card_id}, headers=ALICE)
    assert response.status_code == 202 and response.json()["steps_requested"] == ["analyze_need"]
    run = api.wait(response.json()["run_id"])
    assert [s["name"] for s in run["steps"] if s["status"] == "succeeded"] == ["analyze_need"]


@pytest.mark.parametrize(
    "path, body, status, code",
    [
        ("/api/workflows/triage", {}, 422, "validation_error"),
        ("/api/workflows/triage", {"card_id": 1, "card": {"board_id": 1, "title": "x"}}, 422, "validation_error"),
        ("/api/workflows/triage", {"card_id": 1, "steps": ["teleport"]}, 422, "unknown_step"),
        ("/api/workflows/triage", {"card_id": 1, "steps": ["human_checkpoint"]}, 422, "unknown_step"),
        ("/api/workflows/triage", {"card_id": 1, "extra": 1}, 422, "validation_error"),
        ("/api/workflows/triage", {"card": {"board_id": 1, "title": "  "}}, 422, "validation_error"),
        ("/api/workflows/triage", {"card_id": 999}, 404, "not_found"),
        ("/api/workflows/triage", {"card": {"board_id": 999, "title": "x"}}, 404, "not_found"),
        ("/api/workflows/steps/teleport", {"card_id": 1}, 422, "unknown_step"),
        ("/api/workflows/steps/all", {"card_id": 1}, 422, "unknown_step"),
    ],
)
def test_bad_requests_are_rejected_before_anything_runs(make_api, db, path, body, status, code):
    api = make_api()
    response = api.client.post(path, json=body, headers=ALICE)
    assert response.status_code == status and response.json()["error"]["code"] == code
    with db.session() as s:
        assert s.scalars(select(AIRun).where(AIRun.kind == "workflow")).all() == []


def test_a_rejected_card_payload_creates_no_card(make_api, db):
    api = make_api()
    before = len(api.client.get("/api/cards").json())
    api.client.post(
        "/api/workflows/triage", json={"card": {"board_id": db.ids["board"], "title": "x" * 300}}, headers=ALICE
    )
    assert len(api.client.get("/api/cards").json()) == before


# ---- reading runs -----------------------------------------------------------------------------
def test_get_run_and_list_runs(make_api, db):
    api = make_api()
    card_id = add_card(db, *DISTINCT)
    first = api.triage(card_id=card_id, steps=["score"])["run_id"]
    api.wait(first)
    second = api.triage(card_id=card_id, steps=["tags"])["run_id"]
    api.wait(second)

    listed = api.client.get("/api/workflows/runs", params={"card_id": card_id}).json()
    assert [r["run_id"] for r in listed] == [second, first]
    assert listed[0]["status"] == "succeeded" and listed[0]["outcome"] == "completed"
    assert api.client.get("/api/workflows/runs", params={"card_id": 999}).json() == []
    assert api.client.get("/api/workflows/runs/424242").status_code == 404
    assert api.client.get("/api/workflows/runs/abc").status_code == 422
    step = api.client.get(f"/api/workflows/runs/{first}/").status_code  # trailing slash is not a different route
    assert step in (200, 307, 404)


def test_a_run_can_be_read_while_it_is_still_going(make_api, db):
    release = threading.Event()

    def slow(prompt):
        release.wait(30)
        from ai.llm.mock import MockLLM

        return MockLLM().invoke(prompt).output

    api = make_api(llm=ScriptedLLM({"analyze_need": slow}))
    card_id = add_card(db, *DISTINCT)
    run_id = api.triage(card_id=card_id)["run_id"]
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            body = api.client.get(f"/api/workflows/runs/{run_id}").json()
            steps = {s["name"]: s["status"] for s in body["steps"]}
            if steps["analyze_need"] == "running":
                break
            time.sleep(0.2)
        assert body["status"] == "running" and steps["analyze_need"] == "running"
        assert steps["validate_intake"] == "succeeded" and steps["score"] == "pending"
        assert body["progress"] == {"done": 1, "total": 8}
    finally:
        release.set()
    assert api.wait(run_id)["status"] == "succeeded"


# ---- limits -----------------------------------------------------------------------------------
def test_only_one_run_per_card_at_a_time(make_api, db):
    release = threading.Event()

    def slow(prompt):
        release.wait(30)
        from ai.llm.mock import MockLLM

        return MockLLM().invoke(prompt).output

    api = make_api(llm=ScriptedLLM({"analyze_need": slow}))
    card_id = add_card(db, *DISTINCT)
    first = api.triage(card_id=card_id)["run_id"]
    try:
        again = api.client.post("/api/workflows/triage", json={"card_id": card_id}, headers=ALICE)
        assert again.status_code == 409 and again.json()["error"]["code"] == "run_in_progress"
    finally:
        release.set()
    api.wait(first)
    assert api.client.post("/api/workflows/triage", json={"card_id": card_id}, headers=ALICE).status_code == 202


def test_too_many_concurrent_runs_get_429(make_api, db):
    release = threading.Event()

    def slow(prompt):
        release.wait(30)
        from ai.llm.mock import MockLLM

        return MockLLM().invoke(prompt).output

    api = make_api(llm=ScriptedLLM({"analyze_need": slow}), max_concurrent=1)
    a, b = (
        add_card(db, *DISTINCT),
        add_card(db, "Another request", "Something else entirely about invoices and billing."),
    )
    first = api.triage(card_id=a)["run_id"]
    try:
        second = api.client.post("/api/workflows/triage", json={"card_id": b}, headers=ALICE)
        assert second.status_code == 429 and second.json()["error"]["code"] == "too_many_runs"
    finally:
        release.set()
    api.wait(first)


def test_runs_left_running_by_a_previous_process_are_failed_on_startup(make_api, db):
    with db.session() as s:
        run = AIRun(kind="workflow", card_id=db.ids["saml"], status=RunStatus.running, input={"steps": None})
        s.add(run)
        s.flush()
        step = AIRun(kind="step:score", parent_run_id=run.id, status=RunStatus.running)
        paused = AIRun(kind="workflow", card_id=db.ids["okta"], status=RunStatus.awaiting_human, input={"steps": None})
        s.add_all([step, paused])
        s.commit()
        run_id, step_id, paused_id = run.id, step.id, paused.id
    api = make_api()
    api.triage(card_id=add_card(db, *DISTINCT))  # starts the runtime
    with db.session() as s:
        assert s.get(AIRun, run_id).status is RunStatus.failed and "restart" in s.get(AIRun, run_id).error
        assert s.get(AIRun, step_id).status is RunStatus.failed
        assert s.get(AIRun, paused_id).status is RunStatus.awaiting_human  # waiting for a person: kept


# ---- pausing and resuming ---------------------------------------------------------------------
def test_a_paused_run_exposes_its_question_and_resumes_through_the_api(make_api, db):
    api = make_api(config=UNSURE)
    card_id = add_card(db, *DUPLICATE_OF_SAML)
    run = api.wait(api.triage(card_id=card_id)["run_id"])

    assert run["status"] == "awaiting_human" and run["outcome"] is None
    question = run["pending_action"]
    assert question["kind"] == "confirm_duplicate" and question["card_id"] == card_id
    assert any(c["card_id"] == db.ids["saml"] for c in question["payload"]["candidates"])
    checkpoint = next(s for s in run["steps"] if s["name"] == "human_checkpoint")
    assert checkpoint["status"] == "awaiting_human" and checkpoint["route"] is True
    # a person has not answered yet: nothing was applied
    assert api.client.get(f"/api/cards/{card_id}/suggestions").json() == [] or all(
        s["type"] != "duplicate" for s in api.client.get(f"/api/cards/{card_id}/suggestions").json()
    )

    bad = api.client.post(f"/api/workflows/runs/{run['run_id']}/resume", json={"decision": {"verdict": "maybe"}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_decision"
    assert api.client.post(f"/api/workflows/runs/{run['run_id']}/resume", json={}).status_code == 422

    ok = api.client.post(f"/api/workflows/runs/{run['run_id']}/resume", json={"decision": {"verdict": "duplicate"}})
    assert ok.status_code == 202
    done = api.wait(run["run_id"], until=("succeeded", "failed"))
    assert (done["status"], done["outcome"]) == ("succeeded", "duplicate_proposed")
    assert done["pending_action"] is None
    assert done["human_decisions"][0]["checkpoint"] == "confirm_duplicate"
    types = {s["type"] for s in api.client.get(f"/api/cards/{card_id}/suggestions").json()}
    assert "duplicate" in types


def test_resume_errors(make_api, db):
    api = make_api()
    run = api.wait(api.triage(card_id=add_card(db, *DISTINCT))["run_id"])
    finished = api.client.post(f"/api/workflows/runs/{run['run_id']}/resume", json={"decision": {"actionable": True}})
    assert finished.status_code == 409 and finished.json()["error"]["code"] == "not_waiting"
    assert api.client.post("/api/workflows/runs/424242/resume", json={"decision": {}}).status_code == 404


def test_the_acceptance_flow_end_to_end_in_mock_mode(make_api, db):
    """Submit a card duplicating a seeded one, triage it, see the merge suggestion, accept it: votes consolidate."""
    api = make_api()
    saml = db.ids["saml"]  # seeded: one vote (Alice)
    accepted = api.triage(
        card={"board_id": db.ids["board"], "title": DUPLICATE_OF_SAML[0], "description": DUPLICATE_OF_SAML[1]}
    )
    new_id = accepted["card_id"]
    api.client.post(f"/api/cards/{new_id}/votes", headers=BOB)  # someone already voted for the new request

    run = api.wait(accepted["run_id"])
    assert (run["status"], run["outcome"]) == ("succeeded", "duplicate_proposed")

    suggestions = api.client.get(f"/api/cards/{new_id}/suggestions").json()
    duplicate = next(s for s in suggestions if s["type"] == "duplicate")
    assert duplicate["data"]["other_card_id"] == saml and duplicate["confidence"] >= 0.85 and duplicate["rationale"]
    assert duplicate["data"]["merge"] == {"primary_card_id": saml, "duplicate_card_id": new_id, "votes_on_duplicate": 1}
    # the card is flagged for review
    assert api.client.get(f"/api/cards/{new_id}").json()["needs_review"] is True

    done = api.client.post(f"/api/cards/{new_id}/suggestions/{duplicate['id']}/accept", json={}, headers=ALICE)
    assert done.status_code == 200 and done.json()["status"] == "confirmed"
    assert done.json()["effects"]["votes_moved"] == 1

    primary = api.client.get(f"/api/cards/{saml}").json()
    assert primary["vote_count"] == 2  # Alice's vote plus Bob's, moved from the duplicate
    assert api.client.get(f"/api/cards/{new_id}").json()["vote_count"] == 0
    link = next(link for link in api.client.get(f"/api/cards/{new_id}").json()["links"] if link["card_id"] == saml)
    assert (link["kind"], link["status"]) == ("duplicate", "confirmed")  # the original stays linked
    assert api.client.get(f"/api/cards/{saml}").json()["links"][0]["card_id"] == new_id
    audit = api.client.get(f"/api/cards/{new_id}/audit").json()
    assert {e["action"] for e in audit} == {"suggestion_accepted", "duplicate_merged"}
    assert next(e for e in audit if e["action"] == "duplicate_merged")["detail"]["votes_moved"] == 1
