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
