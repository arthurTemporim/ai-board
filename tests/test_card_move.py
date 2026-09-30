from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Card
from tests.conftest import column_positions, column_titles


def setup_inbox(make_card):
    return [make_card(title) for title in ("A", "B", "C", "D")]


def test_new_cards_are_appended_in_order(client, board, make_card):
    setup_inbox(make_card)
    assert column_titles(client, board, 0) == ["A", "B", "C", "D"]
    assert column_positions(client, board, 0) == [0, 1, 2, 3]


def test_reorder_within_column_down(client, board, make_card):
    a, *_ = setup_inbox(make_card)
    response = client.post(f"/api/cards/{a['id']}/move", json={"column_id": a["column_id"], "position": 2})
    assert response.status_code == 200
    assert response.json()["position"] == 2
    assert column_titles(client, board, 0) == ["B", "C", "A", "D"]
    assert column_positions(client, board, 0) == [0, 1, 2, 3]


def test_reorder_within_column_up(client, board, make_card):
    *_, d = setup_inbox(make_card)
    client.post(f"/api/cards/{d['id']}/move", json={"column_id": d["column_id"], "position": 0})
    assert column_titles(client, board, 0) == ["D", "A", "B", "C"]


def test_move_to_other_column_renumbers_both_columns(client, board, make_card):
    a, b, c, d = setup_inbox(make_card)
    target = board["columns"][3]["id"]
    make_card("X", column=3)
    make_card("Y", column=3)

    response = client.post(f"/api/cards/{b['id']}/move", json={"column_id": target, "position": 1})

    assert response.status_code == 200
    moved = response.json()
    assert (moved["column_id"], moved["position"]) == (target, 1)
    assert column_titles(client, board, 0) == ["A", "C", "D"]
    assert column_positions(client, board, 0) == [0, 1, 2]
    assert column_titles(client, board, 3) == ["X", "B", "Y"]
    assert column_positions(client, board, 3) == [0, 1, 2]


def test_position_beyond_end_is_clamped_to_last(client, board, make_card):
    a, *_ = setup_inbox(make_card)
    target = board["columns"][1]["id"]
    make_card("X", column=1)
    response = client.post(f"/api/cards/{a['id']}/move", json={"column_id": target, "position": 99})
    assert response.json()["position"] == 1
    assert column_titles(client, board, 1) == ["X", "A"]


def test_move_into_empty_column(client, board, make_card):
    a, *_ = setup_inbox(make_card)
    client.post(f"/api/cards/{a['id']}/move", json={"column_id": board["columns"][6]["id"], "position": 0})
    assert column_titles(client, board, 6) == ["A"]
    assert column_positions(client, board, 0) == [0, 1, 2]


def test_move_bumps_version_and_detects_stale_version(client, board, make_card):
    a, *_ = setup_inbox(make_card)
    target = board["columns"][1]["id"]
    moved = client.post(
        f"/api/cards/{a['id']}/move", json={"column_id": target, "position": 0, "version": a["version"]}
    ).json()
    assert moved["version"] == a["version"] + 1

    stale = client.post(
        f"/api/cards/{a['id']}/move", json={"column_id": target, "position": 0, "version": a["version"]}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"


def test_move_to_column_of_another_board_is_rejected_and_changes_nothing(client, board, make_card):
    a, *_ = setup_inbox(make_card)
    other = client.post("/api/boards", json={"name": "Other"}).json()
    foreign_column = client.get(f"/api/boards/{other['id']}/columns").json()[0]["id"]

    response = client.post(f"/api/cards/{a['id']}/move", json={"column_id": foreign_column, "position": 0})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "wrong_board"
    assert column_titles(client, board, 0) == ["A", "B", "C", "D"]


def test_move_unknown_card_or_column_is_404(client, board, make_card):
    a, *_ = setup_inbox(make_card)
    assert client.post("/api/cards/999/move", json={"column_id": a["column_id"], "position": 0}).status_code == 404
    response = client.post(f"/api/cards/{a['id']}/move", json={"column_id": 999, "position": 0})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_negative_position_is_a_validation_error(client, board, make_card):
    a, *_ = setup_inbox(make_card)
    response = client.post(f"/api/cards/{a['id']}/move", json={"column_id": a["column_id"], "position": -1})
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "position"


def test_move_is_atomic_when_it_fails_after_rows_were_written(
    client, session_factory, board, make_card, monkeypatch
):
    """The UPDATEs are flushed, then something fails: the whole move must roll back."""
    a, *_ = setup_inbox(make_card)
    target = board["columns"][3]["id"]
    original = Session.refresh
    flushed_updates = []

    def failing_refresh(self, instance, *args, **kwargs):
        if isinstance(instance, Card):
            flushed_updates.append(instance.id)  # the flush already ran at this point
            raise RuntimeError("boom")
        return original(self, instance, *args, **kwargs)

    monkeypatch.setattr(Session, "refresh", failing_refresh)
    response = client.post(f"/api/cards/{a['id']}/move", json={"column_id": target, "position": 0})
    monkeypatch.undo()

    assert flushed_updates == [a["id"]]
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    with session_factory() as session:
        rows = session.scalars(select(Card).order_by(Card.id)).all()
        inbox = board["columns"][0]["id"]
        assert {r.title: (r.column_id, r.position, r.version) for r in rows} == {
            "A": (inbox, 0, 1),
            "B": (inbox, 1, 1),
            "C": (inbox, 2, 1),
            "D": (inbox, 3, 1),
        }


def test_delete_card_closes_the_gap(client, board, make_card):
    a, b, *_ = setup_inbox(make_card)
    assert client.delete(f"/api/cards/{b['id']}").status_code == 204
    assert column_titles(client, board, 0) == ["A", "C", "D"]
    assert column_positions(client, board, 0) == [0, 1, 2]
