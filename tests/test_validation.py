from __future__ import annotations

import pytest

from tests.conftest import ALICE, BOB


def assert_error_shape(response, status: int, code: str):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    return body["error"]


def test_empty_title_is_rejected_with_field_details(client, board):
    response = client.post("/api/cards", json={"board_id": board["id"], "title": "   "})
    error = assert_error_shape(response, 422, "validation_error")
    assert error["details"][0]["field"] == "title"


@pytest.mark.parametrize("progress", [-1, 101])
def test_progress_must_be_between_0_and_100(client, board, progress):
    response = client.post(
        "/api/cards", json={"board_id": board["id"], "title": "x", "progress": progress}
    )
    error = assert_error_shape(response, 422, "validation_error")
    assert error["details"][0]["field"] == "progress"


def test_unknown_requester_type_is_rejected(client, board):
    response = client.post(
        "/api/cards", json={"board_id": board["id"], "title": "x", "requester_type": "alien"}
    )
    assert_error_shape(response, 422, "validation_error")


def test_unknown_fields_are_rejected(client, board):
    response = client.post("/api/cards", json={"board_id": board["id"], "title": "x", "votes": 50})
    error = assert_error_shape(response, 422, "validation_error")
    assert error["details"][0]["field"] == "votes"


def test_missing_required_field_is_reported(client):
    error = assert_error_shape(client.post("/api/cards", json={"title": "x"}), 422, "validation_error")
    assert error["details"][0]["field"] == "board_id"


def test_bad_tag_color_is_rejected(client, board):
    response = client.post("/api/tags", json={"board_id": board["id"], "name": "x", "color": "red"})
    error = assert_error_shape(response, 422, "validation_error")
    assert error["details"][0]["field"] == "color"


def test_required_field_cannot_be_nulled_in_patch(client, make_card):
    card = make_card()
    response = client.patch(f"/api/cards/{card['id']}", json={"title": None})
    assert_error_shape(response, 422, "validation_error")
    assert client.get(f"/api/cards/{card['id']}").json()["title"] == card["title"]


def test_priority_score_can_be_cleared_with_null(client, make_card):
    card = make_card()
    assert client.patch(f"/api/cards/{card['id']}", json={"priority_score": 7.5}).json()["priority_score"] == 7.5
    assert client.patch(f"/api/cards/{card['id']}", json={"priority_score": None}).json()["priority_score"] is None


def test_non_integer_path_id_is_a_validation_error(client):
    assert_error_shape(client.get("/api/cards/abc"), 422, "validation_error")


def test_unknown_resources_use_the_same_error_shape(client):
    assert_error_shape(client.get("/api/cards/999"), 404, "not_found")
    assert_error_shape(client.get("/api/boards/999"), 404, "not_found")
    assert_error_shape(client.get("/api/nope"), 404, "not_found")


def test_creating_card_on_missing_board_is_404(client):
    assert_error_shape(client.post("/api/cards", json={"board_id": 999, "title": "x"}), 404, "not_found")


def test_tag_from_another_board_is_rejected(client, board):
    other = client.post("/api/boards", json={"name": "Other"}).json()
    tag = client.post("/api/tags", json={"board_id": other["id"], "name": "ux"}).json()
    response = client.post(
        "/api/cards", json={"board_id": board["id"], "title": "x", "tag_ids": [tag["id"]]}
    )
    assert_error_shape(response, 422, "wrong_board")


def test_duplicate_tag_name_on_board_is_conflict_case_insensitive(client, board):
    client.post("/api/tags", json={"board_id": board["id"], "name": "Security"})
    response = client.post("/api/tags", json={"board_id": board["id"], "name": "security"})
    assert_error_shape(response, 409, "duplicate_tag")


def test_duplicate_column_names_in_board_create_are_rejected(client):
    response = client.post("/api/boards", json={"name": "B", "columns": ["Todo", "todo"]})
    assert_error_shape(response, 422, "validation_error")


def test_stale_version_on_patch_is_conflict(client, make_card):
    card = make_card()
    ok = client.patch(f"/api/cards/{card['id']}", json={"title": "New", "version": card["version"]})
    assert ok.status_code == 200
    stale = client.patch(f"/api/cards/{card['id']}", json={"title": "Newer", "version": card["version"]})
    assert_error_shape(stale, 409, "version_conflict")


def test_comments_need_an_author_and_can_only_be_changed_by_them(client, make_card):
    card = make_card()
    url = f"/api/cards/{card['id']}/comments"
    assert_error_shape(client.post(url, json={"body": "hi"}), 401, "actor_required")
    assert_error_shape(client.post(url, json={"body": ""}, headers=ALICE), 422, "validation_error")

    comment = client.post(url, json={"body": "hi"}, headers=ALICE)
    assert comment.status_code == 201
    assert comment.json()["author"]["name"] == "Alice"
    assert client.get(f"/api/cards/{card['id']}").json()["comment_count"] == 1

    cid = comment.json()["id"]
    assert_error_shape(client.patch(f"/api/comments/{cid}", json={"body": "x"}, headers=BOB), 403, "forbidden")
    assert client.patch(f"/api/comments/{cid}", json={"body": "edited"}, headers=ALICE).json()["body"] == "edited"
    assert client.delete(f"/api/comments/{cid}", headers=ALICE).status_code == 204
    assert client.get(f"/api/cards/{card['id']}").json()["comment_count"] == 0


def test_health_and_cors(client):
    assert client.get("/health").json() == {"status": "ok"}
    preflight = client.options(
        "/api/boards",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"
