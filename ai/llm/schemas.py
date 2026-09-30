"""Pydantic output schemas, one per prompt. Every output carries `confidence` and `rationale`.

Schemas are deliberately small and flat: they are the contract the model has to satisfy, and
smaller schemas fail validation less often. Anything computable (totals, weights) is left to code.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Medium = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]


class LLMOutput(BaseModel):
    """Fields every prompt must return."""

    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(ge=0.0, le=1.0, description="0 = guess, 1 = certain")
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] = Field(
        description="One or two sentences explaining the result"
    )


class AnalyzeNeedOutput(LLMOutput):
    underlying_need: Medium = Field(
        description="The job the requester is trying to get done, not the feature asked for"
    )
    problem_statement: Medium = Field(description="One-sentence customer problem, written neutrally")
    is_actionable: bool = Field(description="False when the request is too vague to triage")
    clarifying_questions: list[Short] = Field(default_factory=list, max_length=5)


class JudgeDuplicateOutput(LLMOutput):
    verdict: Literal["duplicate", "related", "distinct"]
    evidence: list[Short] = Field(
        default_factory=list,
        max_length=4,
        description="Short quotes or paraphrases from either request that support the verdict",
    )


class GroupProblemOutput(LLMOutput):
    decision: Literal["existing", "new", "none"]
    existing_problem_id: int | None = None
    new_problem_title: Short | None = None
    new_problem_statement: Medium | None = None

    @model_validator(mode="after")
    def _consistent(self) -> GroupProblemOutput:
        if self.decision == "existing":
            if self.existing_problem_id is None:
                raise ValueError("existing_problem_id is required when decision is 'existing'")
            if self.new_problem_title or self.new_problem_statement:
                raise ValueError("new_problem_* must be empty when decision is 'existing'")
        elif self.decision == "new":
            if not self.new_problem_title or not self.new_problem_statement:
                raise ValueError("new_problem_title and new_problem_statement are required when decision is 'new'")
            if self.existing_problem_id is not None:
                raise ValueError("existing_problem_id must be empty when decision is 'new'")
        elif self.existing_problem_id is not None or self.new_problem_title or self.new_problem_statement:
            raise ValueError("decision 'none' must not include problem fields")
        return self


class CriterionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    rating: int = Field(ge=1, le=5)
    rationale: Short


class ScoreRequestOutput(LLMOutput):
    """Ratings per rubric criterion. Totals and weights are computed in code, never by the model."""

    criteria: list[CriterionScore] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def _unique_keys(self) -> ScoreRequestOutput:
        keys = [c.key for c in self.criteria]
        if len(set(keys)) != len(keys):
            raise ValueError("criteria keys must be unique")
        return self


class TagSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    is_existing: bool = Field(description="True when the name is copied from the existing tag list")
    reason: Short


class SuggestTagsOutput(LLMOutput):
    tags: list[TagSuggestion] = Field(max_length=5)


class DecisionBriefOutput(LLMOutput):
    summary: Medium
    recommendation: Literal["plan", "review", "decline", "needs_info"]
    risks: list[Short] = Field(default_factory=list, max_length=5)
    open_questions: list[Short] = Field(default_factory=list, max_length=5)


class DraftUpdateOutput(LLMOutput):
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    tone: Literal["neutral", "apologetic", "celebratory"]
