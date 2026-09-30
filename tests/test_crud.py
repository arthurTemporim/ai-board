from __future__ import annotations

from tests.conftest import ALICE


def test_board_gets_default_columns_and_detail_groups_cards(client, board, make_card):
    assert [c["name"] for c in board["columns"]][:3] == ["Inbox", "Triaged", "Under Review"]
    assert [c["position"] for c in board["columns"]] == list(range(7))
    make_card("A", column=1)
    detail = client.get(f"/api/boards/{board['id']}").json()
    assert [len(c["cards"]) for c in detail["columns"]] == [0, 1, 0, 0, 0, 0, 0]


def test_card_without_column_goes_to_first_column(client, board):
    card = client.post("/api/cards", json={"board_id": board["id"], "title": "x"}).json()
    assert card["column_id"] == board["columns"][0]["id"]


def test_card_update_tags_and_detail(client, board, make_card):
    tag = client.post("/api/tags", json={"board_id": board["id"], "name": "ux", "color": "#DB2777"}).json()
    card = make_card("x")
    updated = client.patch(f"/api/cards/{card['id']}", json={"tag_ids": [tag["id"]], "progress": 40}).json()
    assert [t["name"] for t in updated["tags"]] == ["ux"]
    assert updated["progress"] == 40
    assert updated["version"] == card["version"] + 1

    client.post(f"/api/cards/{card['id']}/feedback", json={"source": "call", "quote": "please"})
    detail = client.get(f"/api/cards/{card['id']}").json()
    assert detail["feedback"][0]["quote"] == "please"
    assert detail["links"] == [] and detail["problems"] == [] and detail["latest_score"] is None


def test_search_and_tag_filter(client, board, make_card):
    tag = client.post("/api/tags", json={"board_id": board["id"], "name": "ux"}).json()
    make_card("Dark mode", description="night theme", tag_ids=[tag["id"]])
    make_card("Export CSV")
    titles = lambda params: [c["title"] for c in client.get("/api/cards", params=params).json()]  # noqa: E731
    assert titles({"q": "NIGHT"}) == ["Dark mode"]
    assert titles({"tag_id": tag["id"]}) == ["Dark mode"]
    assert titles({"q": "100%"}) == []  # % is escaped, not a wildcard
    assert sorted(titles({"board_id": board["id"]})) == ["Dark mode", "Export CSV"]


def test_column_crud_and_reorder(client, board, make_card):
    new = client.post(f"/api/boards/{board['id']}/columns", json={"name": "Backlog", "position": 0})
    assert new.status_code == 201
    names = [c["name"] for c in client.get(f"/api/boards/{board['id']}/columns").json()]
    assert names[0] == "Backlog" and len(names) == 8

    client.patch(f"/api/columns/{new.json()['id']}", json={"position": 7})
    names = [c["name"] for c in client.get(f"/api/boards/{board['id']}/columns").json()]
    assert names[-1] == "Backlog"

    dup = client.post(f"/api/boards/{board['id']}/columns", json={"name": "backlog"})
    assert dup.status_code == 409


def test_cannot_delete_column_with_cards(client, board, make_card):
    make_card("x")
    response = client.delete(f"/api/columns/{board['columns'][0]['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "column_not_empty"
    assert client.delete(f"/api/columns/{board['columns'][6]['id']}").status_code == 204


def test_delete_board_removes_everything(client, board, make_card):
    card = make_card("x")
    client.post(f"/api/cards/{card['id']}/votes", headers=ALICE)
    assert client.delete(f"/api/boards/{board['id']}").status_code == 204
    assert client.get(f"/api/cards/{card['id']}").status_code == 404
    assert client.get(f"/api/boards/{board['id']}").status_code == 404


def test_feedback_update_and_delete(client, make_card):
    card = make_card()
    entry = client.post(
        f"/api/cards/{card['id']}/feedback", json={"source": "NPS", "quote": "q", "customer_ref": "Acme"}
    ).json()
    patched = client.patch(f"/api/feedback/{entry['id']}", json={"quote": "better"}).json()
    assert patched["quote"] == "better" and patched["customer_ref"] == "Acme"
    assert client.delete(f"/api/feedback/{entry['id']}").status_code == 204
    assert client.get(f"/api/cards/{card['id']}/feedback").json() == []


def test_timestamps_are_timezone_aware(client, make_card):
    assert make_card()["created_at"].endswith("Z")


def test_card_reports_duplicate_related_and_pending_draft_counts(client, session_factory, board, make_card):
    a, b, c = make_card("A"), make_card("B"), make_card("C")
    assert (a["duplicate_count"], a["related_count"], a["pending_draft_count"]) == (0, 0, 0)

    from core.models import CardLink, Draft, DraftKind, DraftStatus, LinkKind

    with session_factory() as session:
        session.add_all(
            [
                CardLink(card_id=a["id"], other_card_id=b["id"], kind=LinkKind.duplicate),
                CardLink(card_id=a["id"], other_card_id=c["id"], kind=LinkKind.related),
                Draft(card_id=a["id"], kind=DraftKind.tag, payload={"name": "ux"}),
                Draft(card_id=a["id"], kind=DraftKind.tag, payload={}, status=DraftStatus.rejected),
            ]
        )
        session.commit()

    cards = {c["title"]: c for c in client.get("/api/cards", params={"board_id": board["id"]}).json()}
    assert (cards["A"]["duplicate_count"], cards["A"]["related_count"], cards["A"]["pending_draft_count"]) == (1, 1, 1)
    # Links count on both ends; only the card with drafts reports them.
    assert (cards["B"]["duplicate_count"], cards["B"]["related_count"], cards["B"]["pending_draft_count"]) == (1, 0, 0)
    assert (cards["C"]["duplicate_count"], cards["C"]["related_count"]) == (0, 1)
    detail = client.get(f"/api/cards/{a['id']}").json()
    links = [(link["card_id"], link["kind"]) for link in detail["links"]]
    assert links == [(b["id"], "duplicate"), (c["id"], "related")]
