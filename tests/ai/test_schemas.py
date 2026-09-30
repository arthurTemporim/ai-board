from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai.llm import schemas
from ai.llm.mock import MockLLM
from ai.llm.registry import default_registry
from tests.ai.samples import PROMPT_NAMES, RUBRIC, SAMPLE_VARIABLES

BASE = {"confidence": 0.8, "rationale": "Because."}


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_mock_output_is_valid_for_each_prompts_schema(name):
    rendered = default_registry().render(name, SAMPLE_VARIABLES[name])
    result = MockLLM().invoke(rendered)

    assert isinstance(result.output, rendered.output_schema)
    # Survives a JSON round trip, which is how it is stored in AIRun.output.
    dumped = result.output.model_dump(mode="json")
    assert rendered.output_schema.model_validate(dumped) == result.output
    assert 0.0 <= result.output.confidence <= 1.0
    assert result.output.rationale.strip()


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_mock_is_deterministic(name):
    rendered = default_registry().render(name, SAMPLE_VARIABLES[name])
    first = MockLLM().invoke(rendered).output
    second = MockLLM().invoke(default_registry().render(name, SAMPLE_VARIABLES[name])).output
    assert first == second


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_every_schema_requires_confidence_and_rationale_and_forbids_extras(name):
    schema = default_registry().load(name).output_schema
    assert issubclass(schema, schemas.LLMOutput)
    required = {k for k, f in schema.model_fields.items() if f.is_required()}
    assert {"confidence", "rationale"} <= required
    assert schema.model_config.get("extra") == "forbid"


@pytest.mark.parametrize(
    "bad", [{"confidence": 1.5}, {"confidence": -0.1}, {"rationale": ""}, {"rationale": "x" * 501}]
)
def test_base_fields_are_bounded(bad):
    with pytest.raises(ValidationError):
        schemas.SuggestTagsOutput.model_validate({"tags": [], **{**BASE, **bad}})


def test_missing_confidence_or_rationale_is_rejected():
    with pytest.raises(ValidationError, match="confidence"):
        schemas.SuggestTagsOutput.model_validate({"tags": [], "rationale": "r"})
    with pytest.raises(ValidationError, match="rationale"):
        schemas.SuggestTagsOutput.model_validate({"tags": [], "confidence": 0.5})


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError, match="Extra inputs"):
        schemas.SuggestTagsOutput.model_validate({"tags": [], "score": 100, **BASE})


# ---- schema-specific rules --------------------------------------------------------------------
def test_judge_duplicate_verdict_is_a_closed_set():
    assert schemas.JudgeDuplicateOutput.model_validate({"verdict": "related", **BASE}).verdict == "related"
    with pytest.raises(ValidationError):
        schemas.JudgeDuplicateOutput.model_validate({"verdict": "merge_them", **BASE})
    with pytest.raises(ValidationError):
        schemas.JudgeDuplicateOutput.model_validate({"verdict": "distinct", "evidence": ["a"] * 5, **BASE})


@pytest.mark.parametrize(
    "data",
    [
        {"decision": "existing"},
        {"decision": "existing", "existing_problem_id": 1, "new_problem_title": "x", "new_problem_statement": "y"},
        {"decision": "new"},
        {"decision": "new", "new_problem_title": "x"},
        {"decision": "new", "new_problem_title": "x", "new_problem_statement": "y", "existing_problem_id": 3},
        {"decision": "none", "existing_problem_id": 3},
        {"decision": "maybe"},
    ],
)
def test_group_problem_rejects_inconsistent_decisions(data):
    with pytest.raises(ValidationError):
        schemas.GroupProblemOutput.model_validate({**data, **BASE})


@pytest.mark.parametrize(
    "data",
    [
        {"decision": "existing", "existing_problem_id": 7},
        {"decision": "new", "new_problem_title": "T", "new_problem_statement": "S"},
        {"decision": "none"},
    ],
)
def test_group_problem_accepts_consistent_decisions(data):
    assert schemas.GroupProblemOutput.model_validate({**data, **BASE}).decision == data["decision"]


def test_score_request_ratings_and_keys():
    ok = {"criteria": [{"key": "a", "rating": 5, "rationale": "r"}], **BASE}
    assert schemas.ScoreRequestOutput.model_validate(ok).criteria[0].rating == 5
    for rating in (0, 6, 3.5, "high"):
        with pytest.raises(ValidationError):
            schemas.ScoreRequestOutput.model_validate(
                {"criteria": [{"key": "a", "rating": rating, "rationale": "r"}], **BASE}
            )
    with pytest.raises(ValidationError, match="unique"):
        schemas.ScoreRequestOutput.model_validate(
            {"criteria": [{"key": "a", "rating": 1, "rationale": "r"}] * 2, **BASE}
        )
    with pytest.raises(ValidationError):
        schemas.ScoreRequestOutput.model_validate({"criteria": [], **BASE})
    with pytest.raises(ValidationError, match="Extra inputs"):
        schemas.ScoreRequestOutput.model_validate({**ok, "total": 100})  # totals are never the model's job


def test_suggest_tags_limits():
    tag = {"name": "ux", "is_existing": True, "reason": "r"}
    assert len(schemas.SuggestTagsOutput.model_validate({"tags": [tag] * 5, **BASE}).tags) == 5
    with pytest.raises(ValidationError):
        schemas.SuggestTagsOutput.model_validate({"tags": [tag] * 6, **BASE})
    with pytest.raises(ValidationError):
        schemas.SuggestTagsOutput.model_validate({"tags": [{**tag, "name": "x" * 41}], **BASE})


def test_decision_brief_recommendation_is_a_closed_set():
    ok = {"summary": "s", "recommendation": "plan", **BASE}
    assert schemas.DecisionBriefOutput.model_validate(ok).recommendation == "plan"
    with pytest.raises(ValidationError):
        schemas.DecisionBriefOutput.model_validate({**ok, "recommendation": "ship_it"})


def test_draft_update_tone_and_lengths():
    ok = {"subject": "s", "body": "b", "tone": "neutral", **BASE}
    assert schemas.DraftUpdateOutput.model_validate(ok).tone == "neutral"
    with pytest.raises(ValidationError):
        schemas.DraftUpdateOutput.model_validate({**ok, "tone": "angry"})
    with pytest.raises(ValidationError):
        schemas.DraftUpdateOutput.model_validate({**ok, "body": "x" * 2001})


def test_analyze_need_question_limit():
    ok = {"underlying_need": "n", "problem_statement": "p", "is_actionable": False, **BASE}
    with pytest.raises(ValidationError):
        schemas.AnalyzeNeedOutput.model_validate({**ok, "clarifying_questions": ["q"] * 6})


# ---- mock behaviour that later phases rely on -------------------------------------------------
def _mock(name, **overrides):
    variables = {**SAMPLE_VARIABLES[name], **overrides}
    return MockLLM().invoke(default_registry().render(name, variables)).output


def test_mock_judge_duplicate_follows_text_overlap():
    same = _mock(
        "judge_duplicate",
        card_a_title="Export reports to CSV",
        card_a_description="Download report tables as CSV files",
        card_b_title="Export reports to CSV",
        card_b_description="Download report tables as CSV files",
    )
    assert same.verdict == "duplicate"
    assert _mock("judge_duplicate").verdict == "distinct"


def test_mock_score_returns_exactly_the_rubric_keys_in_range():
    out = _mock("score_request")
    assert [c.key for c in out.criteria] == [c["key"] for c in RUBRIC]
    assert all(1 <= c.rating <= 5 for c in out.criteria)


def test_mock_group_problem_reuses_a_matching_problem_and_never_invents_ids():
    out = _mock(
        "group_problem",
        card_title="Audit log of admin actions",
        existing_problems=[
            {"id": 7, "title": "Audit and compliance log gaps", "statement": "Admins need an audit log of actions."}
        ],
    )
    assert (out.decision, out.existing_problem_id) == ("existing", 7)
    fresh = _mock(
        "group_problem",
        card_title="Offline mode for field technicians",
        card_description="Technicians work without signal.",
        existing_problems=[],
    )
    assert fresh.decision == "new" and fresh.existing_problem_id is None


def test_mock_suggest_tags_only_uses_existing_tags_mentioned_in_the_text():
    out = _mock("suggest_tags")
    assert [t.name for t in out.tags] == ["ux"] and out.tags[0].is_existing


def test_mock_analyze_need_flags_vague_requests():
    vague = _mock("analyze_need", card_title="Improve it", card_description="Better.")
    assert vague.is_actionable is False and vague.clarifying_questions and vague.confidence < 0.5
    assert _mock("analyze_need").is_actionable is True


@pytest.mark.parametrize("total, expected", [(9, "plan"), (5, "review"), (1, "decline")])
def test_mock_decision_brief_follows_the_score(total, expected):
    assert _mock("decision_brief", score_summary={"total": total, "scale_max": 10}).recommendation == expected


def test_mock_decision_brief_asks_for_info_when_analysis_says_not_actionable():
    out = _mock("decision_brief", analysis={"is_actionable": False})
    assert out.recommendation == "needs_info" and out.open_questions
