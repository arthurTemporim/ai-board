"""The whole graph on your real models (litellm proxy). Skipped unless RUN_LITELLM=1 and LITELLM_BASE_URL is set.

docker compose run --rm --no-deps -e RUN_LITELLM=1 -e MOCK_AI=0 api pytest tests/graph/test_live.py -v
"""

from __future__ import annotations

import os

import pytest

from ai.graph.state import STEPS, GraphConfig
from ai.graph.workflow import Workflow
from ai.llm.factory import OpenAICompatLLM
from tests.graph.conftest import DISTINCT, add_card

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not (os.environ.get("RUN_LITELLM") and os.environ.get("LITELLM_BASE_URL")),
        reason="calls your litellm proxy; set RUN_LITELLM=1 (and LITELLM_BASE_URL) to run",
    ),
]


async def drive(db, tmp_path, card_id: int):
    env = {**db.env, "MOCK_AI": "0", "EMBEDDING_PROVIDER": "litellm"}
    llm = OpenAICompatLLM(timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "180")))
    async with Workflow(
        session_factory=db.factory, env=env, llm=llm, config=GraphConfig(), checkpoint_path=tmp_path / "live.db"
    ) as workflow:
        report = await workflow.start(card_id)
        answers = []
        while report.status == "awaiting_human":  # answer the way a person plausibly would
            kind = report.interrupt["kind"]
            answer = {"verdict": "duplicate"} if kind == "confirm_duplicate" else {"actionable": True}
            answers.append((kind, answer))
            report = await workflow.resume(report.run_id, answer)
    return report, answers


# Known: llama3.2:3b sometimes answers group_problem with the example's problem id instead of one on the
# board. The graph's guard rejects it (nothing is attached) and records this error; see docs/DECISIONS.md.
KNOWN_MODEL_ERRORS = ("the model referred to problem",)


def unexpected(errors):
    return [e for e in errors if not any(known in e["error"] for known in KNOWN_MODEL_ERRORS)]


async def test_a_clear_request_goes_through_the_whole_graph(db, tmp_path):
    card_id = add_card(db, *DISTINCT)
    report, _ = await drive(db, tmp_path, card_id)

    assert report.status == "succeeded", report.errors
    assert report.steps["analyze_need"] == "succeeded" and report.steps["find_related"] == "succeeded"
    assert set(report.steps) >= set(STEPS)
    assert unexpected(report.errors) == [], report.errors
    assert {"analysis", "score", "brief", "comms"} <= {s["kind"] for s in report.suggestions}
    assert 0 <= report.results["score"]["total"] <= 100


async def test_a_request_that_repeats_an_existing_one_is_handled_without_errors(db, tmp_path):
    report, answers = await drive(db, tmp_path, db.ids["okta"])  # a related sign-on card exists on the board
    assert report.status == "succeeded", report.errors
    assert report.outcome in ("completed", "duplicate_proposed", "needs_info")
    assert unexpected(report.errors) == [], report.errors
    assert report.steps["find_related"] == "succeeded"
