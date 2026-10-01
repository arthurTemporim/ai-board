"""Cost and time limits: model-call budget, input caps, tool and run timeouts, secrets in errors."""

from __future__ import annotations

import asyncio

import pytest

from ai.graph.state import STEPS, GraphConfig
from ai.llm.errors import LLMCallError
from ai.llm.mock import MockLLM
from core.models import AIRun, RunStatus
from tests.graph.conftest import DISTINCT, TEST_CONFIG, ScriptedLLM, add_card, runs, step_rows

pytestmark = pytest.mark.anyio


def config(**changes) -> GraphConfig:
    return GraphConfig(**{**TEST_CONFIG.__dict__, **changes})


class Recorder(ScriptedLLM):
    """Mock model that keeps the variables each prompt was rendered with."""

    def __init__(self):
        super().__init__()
        self.variables: dict[str, dict] = {}

    def invoke(self, prompt):
        self.variables[prompt.name] = prompt.variables
        return super().invoke(prompt)


async def test_a_run_cannot_exceed_its_model_call_budget(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    llm = ScriptedLLM()
    async with make_workflow(llm=llm, config=config(max_llm_calls=3)) as wf:
        report = await wf.start(card_id)

    assert len(llm.calls) == 3  # the model was never asked a fourth time
    failed = [s for s, v in report.steps.items() if v == "failed"]
    assert failed and all("max_llm_calls=3" in e["error"] for e in report.errors)
    assert report.status == "succeeded"  # the run finishes; only the over-budget steps failed
    assert report.steps["analyze_need"] == "succeeded"


async def test_the_budget_counts_per_run_not_per_card(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    async with make_workflow(config=config(max_llm_calls=4)) as wf:
        first = await wf.start(card_id, steps="analyze,score")  # 2 calls
        second = await wf.start(card_id, steps="analyze,score")  # 2 more, in a new run
    assert first.errors == [] and second.errors == []


async def test_long_card_text_is_cut_before_it_reaches_the_model(db, make_workflow):
    huge = "word " * 5000
    card_id = add_card(db, "Offline mode for field technicians", huge)
    llm = Recorder()
    async with make_workflow(llm=llm, config=config(max_text_chars=1000)) as wf:
        await wf.start(card_id, steps="analyze,score,tags")

    for prompt in ("analyze_need", "score_request", "suggest_tags"):
        text = llm.variables[prompt]["card_description"]
        assert len(text) <= 1000 + len(" [truncated]") and text.endswith("[truncated]"), prompt


async def test_short_text_is_not_touched(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    llm = Recorder()
    async with make_workflow(llm=llm) as wf:
        await wf.start(card_id, steps="analyze")
    assert llm.variables["analyze_need"]["card_description"] == DISTINCT[1]


async def test_only_a_limited_number_of_feedback_quotes_are_sent(db, make_workflow):
    from core.services.engagement import FeedbackService

    card_id = add_card(db, *DISTINCT)
    with db.session() as s:
        for i in range(30):
            FeedbackService(s).add_feedback(card_id, "Call", f"quote {i} " + "x" * 800, None)
        s.commit()
    llm = Recorder()
    async with make_workflow(llm=llm, config=config(max_feedback_quotes=5)) as wf:
        await wf.start(card_id, steps="analyze")
    quotes = llm.variables["analyze_need"]["feedback_quotes"]
    assert len(quotes) == 5 and all(len(q) <= 500 + len(" [truncated]") for q in quotes)


async def test_the_number_of_candidates_judged_is_capped(db, make_workflow):
    for i in range(12):
        add_card(db, f"Single sign-on variant {i}", "IT requires SAML sign-on before we roll the product out.")
    card_id = add_card(
        db, "Support SAML single sign-on please", "IT requires SAML sign-on before we roll the product out."
    )
    llm = ScriptedLLM()
    async with make_workflow(llm=llm, config=config(top_k=3, min_similarity=0.0)) as wf:
        report = await wf.start(card_id, steps="related")
    assert len(report.results["find_related"]["candidates"]) == 3 and llm.calls.count("judge_duplicate") == 3


def test_config_limits_are_validated():
    for change in (
        {"top_k": 21},
        {"max_llm_calls": 0},
        {"max_text_chars": 10},
        {"max_feedback_quotes": 99},
        {"tool_timeout": 0},
        {"run_timeout": -1},
    ):
        with pytest.raises(ValueError):
            config(**change)


async def test_a_hung_mcp_tool_times_out_instead_of_hanging_the_run(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    async with make_workflow(config=config(tool_timeout=0.5)) as wf:
        real_call = wf.ctx.toolkit.call

        async def hang(name, **arguments):
            if name == "get_rubric":
                await asyncio.sleep(30)
            return await real_call(name, **arguments)

        wf.ctx.toolkit.call = hang  # type: ignore[method-assign]
        report = await wf.start(card_id, steps="score")

    assert report.steps["score"] == "failed"
    assert "'get_rubric' did not answer within 0.5s" in report.errors[0]["error"]


async def test_a_run_that_takes_too_long_is_failed(db, make_workflow):
    card_id = add_card(db, *DISTINCT)

    def slow(prompt):
        import time

        time.sleep(3)
        return MockLLM().invoke(prompt).output

    async with make_workflow(llm=ScriptedLLM({"analyze_need": slow}), config=config(run_timeout=1.0)) as wf:
        with pytest.raises(TimeoutError):
            await wf.start(card_id)
    (workflow,) = runs(db, kind="workflow")
    assert workflow.status is RunStatus.failed and "longer than 1s" in workflow.error


async def test_secrets_in_error_messages_are_not_stored(db, make_workflow, monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "sk-live-SUPERSECRET123456")
    card_id = add_card(db, *DISTINCT)

    def leaky(prompt):
        raise LLMCallError("401 Unauthorized for Bearer sk-live-SUPERSECRET123456 at http://proxy")

    async with make_workflow(llm=ScriptedLLM({"score_request": leaky})) as wf:
        report = await wf.start(card_id, steps="score")

    stored = [r.error for r in runs(db) if r.error] + [e["error"] for e in report.errors]
    assert stored and all("SUPERSECRET" not in text for text in stored)
    assert any("[redacted]" in text for text in stored)
    assert step_rows(db, report.run_id)["score"].error == report.errors[0]["error"]


async def test_every_step_still_runs_with_the_default_limits(db, make_workflow):
    card_id = add_card(db, *DISTINCT)
    async with make_workflow(config=GraphConfig(min_similarity=0.05)) as wf:
        report = await wf.start(card_id)
    assert report.steps == {s: "succeeded" for s in STEPS} and isinstance(AIRun, type)
