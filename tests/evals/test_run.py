"""The eval harness end to end on a few cards, in mock mode (real mode is run by `make eval`)."""

from __future__ import annotations

import pytest

from ai.evals import run as eval_run
from ai.evals.report import render_markdown
from ai.graph.state import GraphConfig

pytestmark = pytest.mark.anyio

# Cards 1, 2, 3 are the three sign-on requests; with a low similarity floor the mock embedder surfaces them.
CONFIG = GraphConfig(min_similarity=0.0, top_k=8)


async def test_a_mock_run_scores_a_subset_and_writes_a_report(tmp_path):
    result = await eval_run.evaluate("mock", CONFIG, card_ids=[1, 2, 3])

    assert result.mode == "mock" and result.cards_run == 3 and result.llm_model == "mock"
    assert result.embedder == "mock-hash-256" and result.prompt.startswith("judge_duplicate@")
    assert result.errors == [] and result.candidates_judged > 0
    s = result.scores
    assert sum(sum(row.values()) for row in s.confusion.values()) == s.pairs  # every pair is accounted for once
    assert s.duplicate.actual == 3  # the three sso pairs involve cards 1-3

    from datetime import UTC, datetime

    markdown = render_markdown(
        [result], generated=datetime(2026, 1, 2, tzinfo=UTC), skipped_real=True, subset=[1, 2, 3]
    )
    for expected in (
        "# Evaluation results",
        "## Mock mode",
        "| duplicate (linked) |",
        "Confusion matrix",
        "Not run: no model is configured",
        "Partial run",
    ):
        assert expected in markdown


async def test_the_eval_does_not_touch_the_configured_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'dev.db'}")
    monkeypatch.setenv("MOCK_AI", "0")  # restored afterwards, whatever the eval does to it
    await eval_run.evaluate("mock", CONFIG, card_ids=[1])
    assert not (tmp_path / "dev.db").exists()
    import os

    assert os.environ["DATABASE_URL"].endswith("dev.db") and os.environ["MOCK_AI"] == "0"


def test_mode_selection(monkeypatch):
    for var in ("LITELLM_BASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert eval_run.modes_to_run("auto") == ["mock"] and not eval_run.real_mode_available()
    assert eval_run.modes_to_run("both") == ["mock", "real"] and eval_run.modes_to_run("real") == ["real"]
    monkeypatch.setenv("LITELLM_BASE_URL", "http://proxy:4000")
    assert eval_run.real_mode_available() and eval_run.modes_to_run("auto") == ["mock", "real"]


async def test_real_mode_without_a_configured_model_is_a_clear_error(monkeypatch, capsys):
    for var in ("LITELLM_BASE_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    code = await eval_run.main_async(eval_run.parse_args(["--mode", "real", "--no-write"]))
    assert code == 2 and "Real mode needs" in capsys.readouterr().err


async def test_the_cli_writes_the_file_and_can_gate_on_recall(tmp_path, capsys):
    out = tmp_path / "results.md"
    args = ["--mode", "mock", "--cards", "1,2,3", "--min-similarity", "0.0", "--top-k", "8", "--output", str(out)]
    assert await eval_run.main_async(eval_run.parse_args(args)) == 0
    assert out.read_text().startswith("# Evaluation results") and "mock mode" in capsys.readouterr().out
    assert await eval_run.main_async(eval_run.parse_args([*args, "--fail-below-recall", "1.01"])) == 1
    assert (
        await eval_run.main_async(
            eval_run.parse_args([*args, "--no-write", "--link-threshold", "0.9", "--merge-threshold", "0.5"])
        )
        == 2
    )
