from __future__ import annotations

from tests.conftest import ALICE, BOB


def test_vote_counts_and_shows_in_card(client, make_card):
    card = make_card("Dark mode")
    response = client.post(f"/api/cards/{card['id']}/votes", headers=ALICE)
    assert response.status_code == 201
    assert response.json() == {"card_id": card["id"], "vote_count": 1, "voted": True}
    assert client.get(f"/api/cards/{card['id']}").json()["vote_count"] == 1


def test_same_user_cannot_vote_twice(client, make_card):
    card = make_card()
    assert client.post(f"/api/cards/{card['id']}/votes", headers=ALICE).status_code == 201

    second = client.post(f"/api/cards/{card['id']}/votes", headers=ALICE)

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_voted"
    assert client.get(f"/api/cards/{card['id']}/votes").json()["vote_count"] == 1


def test_different_users_can_each_vote_once(client, make_card):
    card = make_card()
    client.post(f"/api/cards/{card['id']}/votes", headers=ALICE)
    response = client.post(f"/api/cards/{card['id']}/votes", headers=BOB)
    assert response.status_code == 201
    assert response.json()["vote_count"] == 2


def test_vote_summary_reports_whether_the_caller_voted(client, make_card):
    card = make_card()
    client.post(f"/api/cards/{card['id']}/votes", headers=ALICE)
    url = f"/api/cards/{card['id']}/votes"
    assert client.get(url, headers=ALICE).json()["voted"] is True
    assert client.get(url, headers=BOB).json()["voted"] is False
    assert client.get(url).json()["voted"] is False


def test_remove_vote_allows_voting_again(client, make_card):
    card = make_card()
    url = f"/api/cards/{card['id']}/votes"
    client.post(url, headers=ALICE)
    removed = client.delete(url, headers=ALICE)
    assert removed.status_code == 200
    assert removed.json() == {"card_id": card["id"], "vote_count": 0, "voted": False}
    assert client.post(url, headers=ALICE).status_code == 201


def test_remove_missing_vote_is_404(client, make_card):
    card = make_card()
    response = client.delete(f"/api/cards/{card['id']}/votes", headers=ALICE)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "vote_not_found"


def test_voting_requires_a_known_user(client, make_card):
    card = make_card()
    url = f"/api/cards/{card['id']}/votes"

    missing = client.post(url)
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "actor_required"

    unknown = client.post(url, headers={"X-User-Id": "999"})
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "unknown_actor"


def test_vote_on_missing_card_is_404(client):
    assert client.post("/api/cards/999/votes", headers=ALICE).status_code == 404
