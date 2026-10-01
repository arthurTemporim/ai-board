from __future__ import annotations

from datetime import timedelta

import pytest

from core.models import AuditEntry, Card
from core.models.types import utcnow
from core.services.metrics import Baseline, compute_metrics


def entry(session, card_id, action, minutes_after_creation=None, **detail):
    when = utcnow()
    if minutes_after_creation is not None:
        card = session.get(Card, card_id)
        when = card.created_at + timedelta(minutes=minutes_after_creation)
    row = AuditEntry(card_id=card_id, actor_id=1, action=action, detail=detail, created_at=when)
    session.add(row)
    return row


def test_empty_database_gives_no_numbers_not_zeros(client):
    body = client.get("/api/metrics").json()
    assert body["time_to_first_decision"] == {
        "cards": 0,
        "mean_minutes": None,
        "median_minutes": None,
        "slowest_minutes": None,
    }
    assert body["acceptance"]["rate"] is None and body["score_overrides"]["rate"] is None
    assert body["duplicates"]["merged"] == 0 and body["total_decisions"] == 0 and body["demo_entries"] == 0


def test_metrics_are_computed_from_the_audit_log(client, session_factory, make_card):
    a, b, c = (make_card(t) for t in ("A", "B", "C"))
    with session_factory() as s:
        # card A: first decided 10 minutes after creation; B: 30 minutes; C: immediately
        entry(s, a["id"], "suggestion_accepted", 10, type="tag", edited=False)
        entry(s, a["id"], "suggestion_rejected", 12, type="related")
        entry(s, b["id"], "suggestion_accepted", 30, type="duplicate", edited=False)
        entry(s, b["id"], "duplicate_merged", 30, votes_moved=3)
        entry(s, b["id"], "suggestion_accepted", 31, type="score", edited=False)
        entry(s, b["id"], "suggestion_accepted", 32, type="brief", edited=True)
        entry(s, b["id"], "score_override", 33, reason="x")
        entry(s, b["id"], "suggestion_rejected", 34, type="score")
        entry(s, c["id"], "suggestion_rejected", 0, type="tag", demo=True)
        s.commit()

    m = client.get("/api/metrics").json()
    # card A: 10 min, B: 30 min, C: 0 min (decided the moment it was created)
    assert m["time_to_first_decision"] == {
        "cards": 3,
        "mean_minutes": 13.3,
        "median_minutes": 10.0,
        "slowest_minutes": 30.0,
    }

    acc = m["acceptance"]
    # kept = 4 accepts (tag, duplicate, score, brief) + 1 override; rejected = 3
    assert (acc["accepted"], acc["rejected"], acc["decided"]) == (5, 3, 8)
    assert acc["rate"] == 0.625
    assert acc["accepted_without_changes_rate"] == round(
        (5 - 2) / 8, 4
    )  # the edited brief and the override were changed
    assert acc["by_type"]["score"] == {"accepted": 2, "rejected": 1, "rate": pytest.approx(0.6667, abs=1e-4)}
    assert acc["by_type"]["tag"] == {"accepted": 1, "rejected": 1, "rate": 0.5}
    assert m["duplicates"] == {
        "merged": 1,
        "votes_moved": 3,
        "duplicates_confirmed_as_suggested": 1,
        "duplicates_rejected": 0,
    }
    assert m["score_overrides"] == {"overridden": 1, "kept": 2, "rate": 0.5, "scores_rejected": 1}
    # a merge is recorded next to its acceptance, so it is not counted as a decision of its own
    assert m["demo_entries"] == 1 and m["total_decisions"] == 8


def test_time_to_first_decision_uses_the_first_decision_per_card(client, session_factory, make_card):
    a, b = make_card("A"), make_card("B")
    with session_factory() as s:
        entry(s, a["id"], "suggestion_accepted", 20, type="tag", edited=False)
        entry(s, a["id"], "suggestion_accepted", 500, type="score", edited=False)  # later decisions do not count
        entry(s, b["id"], "score_override", 40)
        entry(s, b["id"], "duplicate_merged", 5, votes_moved=0)  # a merge is not a decision of its own
        s.commit()
        t = compute_metrics(s)["time_to_first_decision"]
    assert t == {"cards": 2, "mean_minutes": 30.0, "median_minutes": 30.0, "slowest_minutes": 40.0}


def test_decisions_before_creation_are_clamped_and_unknown_cards_ignored(client, session_factory, make_card):
    a = make_card("A")
    with session_factory() as s:
        entry(s, a["id"], "suggestion_accepted", -5, type="tag", edited=False)
        entry(s, None, "suggestion_accepted", None, type="tag", edited=False)  # card deleted since
        s.commit()
        t = compute_metrics(s)["time_to_first_decision"]
    assert t["cards"] == 1 and t["mean_minutes"] == 0.0


def test_the_baseline_is_labelled_as_an_assumption_and_configurable(client, monkeypatch, session_factory):
    base = client.get("/api/metrics").json()["baseline"]
    assert base["is_assumption"] is True and base["comparable"] is False
    assert base["manual_triage_minutes"] == 15.0 and "assumption, not a measurement" in base["description"]

    monkeypatch.setenv("METRICS_BASELINE_TRIAGE_MINUTES", "25")
    assert client.get("/api/metrics").json()["baseline"]["manual_triage_minutes"] == 25.0
    monkeypatch.setenv("METRICS_BASELINE_TRIAGE_MINUTES", "lots")
    assert client.get("/api/metrics").json()["baseline"]["manual_triage_minutes"] == 15.0
    with session_factory() as s:
        assert compute_metrics(s, Baseline(7))["baseline"]["manual_triage_minutes"] == 7


def test_every_metric_has_a_definition(client):
    definitions = client.get("/api/metrics").json()["definitions"]
    assert set(definitions) == {"time_to_first_decision", "acceptance", "duplicates", "score_overrides"}
    assert all(len(text) > 40 for text in definitions.values())
