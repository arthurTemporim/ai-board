from __future__ import annotations

import pytest
from sqlalchemy import select

from ai.llm.errors import LLMCallError
from ai.llm.factory import LLMResult
from ai.llm.mock import MockLLM
from ai.llm.registry import default_registry
from ai.llm.runner import run_prompt
from ai.llm.schemas import SuggestTagsOutput
from core.models import AIRun, RunStatus
from tests.ai.samples import SAMPLE_VARIABLES


class FixedLLM:
    def __init__(self, output, model="claude-test", tokens=(11, 7)):
        self.output, self.model, self.tokens = output, model, tokens

    def model_for(self, prompt):
        return self.model

    def invoke(self, prompt):
        return LLMResult(self.output, *self.tokens)


class FailingLLM:
    def model_for(self, prompt):
        return "claude-test"

    def invoke(self, prompt):
        raise LLMCallError("upstream is down")


def test_successful_run_is_logged_in_ai_run(session_factory):
    variables = SAMPLE_VARIABLES["suggest_tags"]
    expected_hash = default_registry().render("suggest_tags", variables).input_hash
    output = SuggestTagsOutput(tags=[], confidence=0.42, rationale="because")

    with session_factory() as session:
        result = run_prompt(
            "suggest_tags", variables, session=session, card_id=None, llm=FixedLLM(output), created_by_id=None
        )
        session.commit()
        run = session.scalars(select(AIRun)).one()

    assert result.run_id == run.id and result.output is output
    assert run.kind == "suggest_tags"
    assert (run.prompt_name, run.prompt_version) == ("suggest_tags", default_registry().active_version("suggest_tags"))
    assert run.model == "claude-test"
    assert run.input_hash == result.input_hash == expected_hash
    assert run.input == {"variables": variables}
    assert run.output == output.model_dump(mode="json")
    assert run.confidence == 0.42
    assert run.latency_ms is not None and run.latency_ms >= 0 and run.latency_ms == result.latency_ms
    assert (run.tokens_in, run.tokens_out) == (11, 7)
    assert run.status is RunStatus.succeeded
    assert run.error is None and run.finished_at is not None


def test_run_is_linked_to_its_card_and_parent(session_factory, client, make_card):
    card = make_card("A request")
    with session_factory() as session:
        parent = run_prompt(
            "suggest_tags", SAMPLE_VARIABLES["suggest_tags"], session=session, card_id=card["id"], llm=MockLLM()
        )
        child = run_prompt(
            "suggest_tags",
            SAMPLE_VARIABLES["suggest_tags"],
            session=session,
            card_id=card["id"],
            parent_run_id=parent.run_id,
            created_by_id=1,
            llm=MockLLM(),
        )
        session.commit()
        run = session.get(AIRun, child.run_id)
        assert (run.card_id, run.parent_run_id, run.created_by_id) == (card["id"], parent.run_id, 1)


def test_the_model_is_the_effective_one_not_the_frontmatter_default(session_factory):
    with session_factory() as session:
        result = run_prompt("suggest_tags", SAMPLE_VARIABLES["suggest_tags"], session=session, llm=MockLLM())
        assert result.model == "mock" and session.scalars(select(AIRun)).one().model == "mock"


def test_identical_inputs_share_a_hash_and_different_inputs_do_not(session_factory):
    with session_factory() as session:
        a = run_prompt("suggest_tags", SAMPLE_VARIABLES["suggest_tags"], session=session, llm=MockLLM())
        b = run_prompt("suggest_tags", SAMPLE_VARIABLES["suggest_tags"], session=session, llm=MockLLM())
        c = run_prompt(
            "suggest_tags", {**SAMPLE_VARIABLES["suggest_tags"], "card_title": "Other"}, session=session, llm=MockLLM()
        )
        assert a.input_hash == b.input_hash != c.input_hash
        assert a.run_id != b.run_id  # still one row per call


def test_failed_run_is_recorded_then_the_error_propagates(session_factory):
    with session_factory() as session:
        with pytest.raises(LLMCallError, match="upstream is down"):
            run_prompt("suggest_tags", SAMPLE_VARIABLES["suggest_tags"], session=session, llm=FailingLLM())
        run = session.scalars(select(AIRun)).one()
        assert run.status is RunStatus.failed
        assert "LLMCallError: upstream is down" in run.error
        assert run.output is None and run.confidence is None
        assert run.latency_ms is not None and run.finished_at is not None
        assert run.input_hash and run.prompt_name == "suggest_tags"


def test_works_without_a_session_and_logs_nothing(session_factory):
    result = run_prompt("suggest_tags", SAMPLE_VARIABLES["suggest_tags"], llm=MockLLM())
    assert result.run_id is None and isinstance(result.output, SuggestTagsOutput)
    with session_factory() as session:
        assert session.scalars(select(AIRun)).all() == []


def test_explicit_version_is_used_and_recorded(tmp_path, session_factory):
    from ai.llm.registry import PromptRegistry
    from tests.ai.test_registry import BODY, write_prompt, write_registry

    def front(version: int) -> str:
        return (
            f"---\nname: suggest_tags\nversion: {version}\nmodel: m{version}\ntemperature: 0\n"
            "input_variables: [topic, {name: note, untrusted: true}]\n"
            "output_schema: ai.llm.schemas:SuggestTagsOutput\n---\n"
        )

    for version in (1, 2):
        write_prompt(tmp_path, name="suggest_tags", version=version, front=front(version), body=BODY)
    write_registry(tmp_path, {"suggest_tags": {"active": 1}})
    registry = PromptRegistry(tmp_path)
    output = SuggestTagsOutput(tags=[], confidence=0.5, rationale="r")
    variables = {"topic": "a", "note": "b"}

    with session_factory() as session:
        run_prompt("suggest_tags", variables, session=session, llm=FixedLLM(output), registry=registry)
        run_prompt("suggest_tags", variables, session=session, llm=FixedLLM(output), registry=registry, version=2)
        versions = [r.prompt_version for r in session.scalars(select(AIRun).order_by(AIRun.id))]
        assert versions == [1, 2]


def test_bad_variables_fail_before_anything_is_logged(session_factory):
    from ai.llm.errors import PromptVariableError

    with session_factory() as session:
        with pytest.raises(PromptVariableError, match="missing variables"):
            run_prompt("suggest_tags", {"card_title": "x"}, session=session, llm=MockLLM())
        assert session.scalars(select(AIRun)).all() == []
