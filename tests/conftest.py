from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import core.models  # noqa: F401
from apps.api.deps import get_session
from apps.api.main import create_app
from core.db import Base, make_engine, make_session_factory, session_scope
from core.models import User


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture
def client(session_factory) -> Iterator[TestClient]:
    app = create_app()

    def override_session() -> Iterator[Session]:
        with session_scope(session_factory) as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with session_factory() as session:
        session.add_all([User(name="Alice", role="pm"), User(name="Bob", role="sales")])
        session.commit()
    # Unexpected errors should come back as 500 responses, not be re-raised into the test.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


ALICE = {"X-User-Id": "1"}
BOB = {"X-User-Id": "2"}


@pytest.fixture
def board(client) -> dict:
    """A board with the default 7 columns."""
    response = client.post("/api/boards", json={"name": "Product"})
    assert response.status_code == 201, response.text
    board = response.json()
    board["columns"] = client.get(f"/api/boards/{board['id']}/columns").json()
    return board


@pytest.fixture
def make_card(client, board):
    def _make(title: str = "A request", column: int = 0, **extra) -> dict:
        body = {"board_id": board["id"], "column_id": board["columns"][column]["id"], "title": title}
        response = client.post("/api/cards", json={**body, **extra})
        assert response.status_code == 201, response.text
        return response.json()

    return _make


def column_titles(client, board: dict, column: int) -> list[str]:
    """Card titles in a column, in board order."""
    detail = client.get(f"/api/boards/{board['id']}").json()
    return [c["title"] for c in detail["columns"][column]["cards"]]


def column_positions(client, board: dict, column: int) -> list[int]:
    detail = client.get(f"/api/boards/{board['id']}").json()
    return [c["position"] for c in detail["columns"][column]["cards"]]
