"""Pydantic request/response models. Inputs reject unknown fields and explicit nulls on required fields."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from core.models import LinkKind, LinkStatus, RequesterType

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ShortName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
TagName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Fields that may be omitted in a PATCH but must not be set to null.
    non_nullable: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _no_null_for_required(self):
        bad = [f for f in self.model_fields_set if f in self.non_nullable and getattr(self, f) is None]
        if bad:
            raise ValueError(f"{', '.join(sorted(bad))} cannot be null")
        return self


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- errors -----------------------------------------------------------------------------
class ErrorDetail(BaseModel):
    field: str | None = None
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---- users ------------------------------------------------------------------------------
class UserRead(Output):
    id: int
    name: str
    role: str


# ---- tags -------------------------------------------------------------------------------
class TagRead(Output):
    id: int
    board_id: int
    name: str
    color: str


class TagCreate(Input):
    board_id: int
    name: TagName
    color: Color = "#6B7280"


class TagUpdate(Input):
    non_nullable: ClassVar[frozenset[str]] = frozenset({"name", "color"})
    name: TagName | None = None
    color: Color | None = None


# ---- boards and columns -----------------------------------------------------------------
class ColumnRead(Output):
    id: int
    board_id: int
    name: str
    position: int
    wip_limit: int | None


class ColumnCreate(Input):
    name: ShortName
    wip_limit: int | None = Field(None, ge=1, le=1000)
    position: int | None = Field(None, ge=0)


class ColumnUpdate(Input):
    non_nullable: ClassVar[frozenset[str]] = frozenset({"name", "position"})
    name: ShortName | None = None
    wip_limit: int | None = Field(None, ge=1, le=1000)
    position: int | None = Field(None, ge=0)


class BoardRead(Output):
    id: int
    name: str
    description: str
    created_at: datetime


class BoardCreate(Input):
    name: Name
    description: str = Field("", max_length=2000)
    columns: list[ShortName] | None = Field(None, max_length=30)

    @model_validator(mode="after")
    def _unique_columns(self):
        if self.columns:
            lowered = [c.lower() for c in self.columns]
            if len(set(lowered)) != len(lowered):
                raise ValueError("column names must be unique")
        return self


class BoardUpdate(Input):
    non_nullable: ClassVar[frozenset[str]] = frozenset({"name", "description"})
    name: Name | None = None
    description: str | None = Field(None, max_length=2000)


# ---- cards ------------------------------------------------------------------------------
class CardRead(Output):
    id: int
    board_id: int
    column_id: int
    position: int
    title: str
    description: str
    requester_type: RequesterType
    progress: int
    priority_score: float | None
    version: int
    created_by_id: int | None
    created_at: datetime
    updated_at: datetime
    tags: list[TagRead]
    vote_count: int
    comment_count: int
    duplicate_count: int
    related_count: int
    pending_draft_count: int
    pending_suggestion_count: int
    needs_review: bool


class BoardColumnView(ColumnRead):
    cards: list[CardRead]


class BoardDetail(BoardRead):
    columns: list[BoardColumnView]


class CardCreate(Input):
    board_id: int
    column_id: int | None = None
    position: int | None = Field(None, ge=0)
    title: Title
    description: str = Field("", max_length=10000)
    requester_type: RequesterType = RequesterType.unknown
    progress: int = Field(0, ge=0, le=100)
    tag_ids: list[int] = Field(default_factory=list, max_length=20)


class CardUpdate(Input):
    non_nullable: ClassVar[frozenset[str]] = frozenset(
        {"title", "description", "requester_type", "progress", "tag_ids"}
    )
    title: Title | None = None
    description: str | None = Field(None, max_length=10000)
    requester_type: RequesterType | None = None
    progress: int | None = Field(None, ge=0, le=100)
    priority_score: float | None = Field(None, ge=0, le=100)
    tag_ids: list[int] | None = Field(None, max_length=20)
    version: int | None = Field(None, ge=1, description="Send the version you last read to detect conflicts")


class CardMove(Input):
    column_id: int
    position: int = Field(ge=0, description="Zero-based index in the target column (clamped)")
    version: int | None = Field(None, ge=1)


class CommentRead(Output):
    id: int
    card_id: int
    author: UserRead
    body: str
    created_at: datetime


class FeedbackRead(Output):
    id: int
    card_id: int
    source: str
    quote: str
    customer_ref: str | None
    created_at: datetime


class LinkRead(BaseModel):
    card_id: int
    title: str
    kind: LinkKind
    status: LinkStatus


class ProblemRead(BaseModel):
    id: int
    title: str
    status: LinkStatus


class ScoreRead(Output):
    id: int
    rubric_name: str
    rubric_version: int
    per_criterion: dict
    total: float
    created_at: datetime


class CardDetail(CardRead):
    comments: list[CommentRead]
    feedback: list[FeedbackRead]
    links: list[LinkRead]
    problems: list[ProblemRead]
    latest_score: ScoreRead | None


# ---- comments, feedback, votes ----------------------------------------------------------
class CommentCreate(Input):
    body: Body


class CommentUpdate(Input):
    body: Body


class FeedbackCreate(Input):
    source: ShortName
    quote: Body
    customer_ref: str | None = Field(None, max_length=120)


class FeedbackUpdate(Input):
    non_nullable: ClassVar[frozenset[str]] = frozenset({"source", "quote"})
    source: ShortName | None = None
    quote: Body | None = None
    customer_ref: str | None = Field(None, max_length=120)


class VoteSummaryRead(Output):
    card_id: int
    vote_count: int
    voted: bool


# ---- workflows ----------------------------------------------------------------------------
class TriageCard(Input):
    """A new card to create and triage in one call."""

    board_id: int
    title: Title
    description: str = Field("", max_length=10000)
    requester_type: RequesterType = RequesterType.unknown
    column_id: int | None = None
    tag_ids: list[int] = Field(default_factory=list, max_length=20)


class TriageRequest(Input):
    card_id: int | None = Field(None, ge=1, description="Triage this existing card")
    card: TriageCard | None = Field(None, description="Or create this card first, then triage it")
    steps: list[str] | None = Field(
        None,
        max_length=8,
        description="Steps to run (names or aliases such as analyze, score, tags); omit to run every step",
    )

    @model_validator(mode="after")
    def _exactly_one_target(self):
        if (self.card_id is None) == (self.card is None):
            raise ValueError("Give exactly one of card_id or card")
        return self


class StepRequest(Input):
    card_id: int | None = Field(None, ge=1)
    card: TriageCard | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self):
        if (self.card_id is None) == (self.card is None):
            raise ValueError("Give exactly one of card_id or card")
        return self


class RunAccepted(BaseModel):
    run_id: int
    card_id: int
    status: str
    steps_requested: list[str] | None
    card_created: bool


class RunStep(BaseModel):
    name: str
    status: str = Field(description="pending | running | succeeded | failed | skipped | awaiting_human | not_requested")
    requested: bool
    route: bool = False
    started_at: datetime | None
    finished_at: datetime | None
    latency_ms: int | None
    result: dict | None = None
    error: str | None = None
    reason: str | None = Field(None, description="Why a step was skipped")


class RunProgress(BaseModel):
    done: int
    total: int


class RunRead(BaseModel):
    run_id: int
    card_id: int | None
    status: str = Field(description="running | awaiting_human | succeeded | failed")
    outcome: str | None = Field(None, description="completed | needs_info | duplicate_proposed | failed")
    steps: list[RunStep]
    progress: RunProgress
    pending_action: dict | None = Field(None, description="The question a person must answer to resume the run")
    human_decisions: list[dict]
    errors: list[dict]
    created_at: datetime
    finished_at: datetime | None


class RunSummary(BaseModel):
    run_id: int
    card_id: int | None
    status: str
    outcome: str | None
    created_at: datetime
    finished_at: datetime | None


class ResumeRequest(Input):
    decision: dict = Field(
        description='Answer to pending_action, e.g. {"verdict": "duplicate"} or {"actionable": true}'
    )


# ---- suggestions ---------------------------------------------------------------------------
class SuggestionRead(BaseModel):
    id: str = Field(description="draft:N, link:N or problem:CARD:PROBLEM")
    type: str
    status: str
    confidence: float | None
    rationale: str | None
    data: dict
    created_at: datetime
    run_id: int | None
    editable: list[str]


class ScoreOverride(Input):
    ratings: dict[str, int] = Field(min_length=1, max_length=12, description="criterion key -> new rating")
    reason: str = Field(min_length=3, max_length=500, description="Why the AI's ratings are wrong (required)")


class AcceptRequest(Input):
    edits: dict | None = Field(None, description="New values for the suggestion's editable fields")
    override: ScoreOverride | None = Field(None, description="Score suggestions only: change ratings, with a reason")


class RejectRequest(Input):
    reason: str | None = Field(None, max_length=500)


class DecisionRead(BaseModel):
    suggestion_id: str
    status: str
    message: str
    effects: dict
    audit_id: int | None


class AuditRead(Output):
    id: int
    card_id: int | None
    actor_id: int | None
    action: str
    detail: dict
    created_at: datetime
