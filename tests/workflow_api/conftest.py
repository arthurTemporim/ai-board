"""API tests that run the real graph: a file SQLite database, real MCP server processes, the mock model."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai.graph.state import GraphConfig
from apps.api.deps import get_session
from apps.api.main import create_app
from apps.api.workflows import WorkflowRuntime
from core.db import session_scope
from core.models import User
from tests.graph.conftest import TEST_CONFIG, ScriptedLLM
from tests.mcp.conftest import Db, anyio_backend, db  # noqa: F401

ALICE = {"X-User-Id": "1"}
BOB = {"X-User-Id": "2"}
TERMINAL = ("succeeded", "failed", "awaiting_human")


@dataclass
class Api:
    client: TestClient
    db: Db
    runtime: WorkflowRuntime

    def wait(self, run_id: int, until: tuple[str, ...] = TERMINAL, timeout: float = 90) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = self.client.get(f"/api/workflows/runs/{run_id}").json()
            if body["status"] in until:
                return body
            time.sleep(0.2)
        raise AssertionError(f"run {run_id} did not reach {until} in {timeout}s: {body}")

    def triage(self, **body: Any) -> dict[str, Any]:
        response = self.client.post("/api/workflows/triage", json=body, headers=ALICE)
        assert response.status_code == 202, response.text
        return response.json()


@pytest.fixture
def make_api(db, tmp_path):  # noqa: F811
    with db.session() as session:
        session.add(User(name="Bob", role="sales"))
        session.commit()
    stack: list[TestClient] = []

    def make(*, llm=None, config: GraphConfig | None = None, max_concurrent: int | None = None) -> Api:
        runtime = WorkflowRuntime(
            session_factory=db.factory,
            env=db.env,
            llm=llm or ScriptedLLM(),
            config=config or TEST_CONFIG,
            checkpoint_path=tmp_path / "api-checkpoints.db",
            max_concurrent=max_concurrent,
        )
        app = create_app(runtime)

        def override() -> Iterator[Any]:
            with session_scope(db.factory) as session:
                yield session

        app.dependency_overrides[get_session] = override
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        stack.append(client)
        return Api(client, db, runtime)

    yield make
    for client in stack:
        client.__exit__(None, None, None)
