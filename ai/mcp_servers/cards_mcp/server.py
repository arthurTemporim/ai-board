"""cards_mcp: read cards and their context, and record AI proposals about them.

Write tools here only ever create *suggestions*: pending drafts, or link/problem attachments with
status "suggested". None of them changes the card itself. There is deliberately no tool that applies
a suggestion; that is a human action in the API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ai.mcp_servers.common import (
    CardId,
    Confidence,
    Rationale,
    RunId,
    SuggestionOut,
    new_server,
)
from core.db import session_scope
from core.errors import ConflictError, InvalidInputError, NotFoundError
from core.models import Card, DraftKind, DraftStatus, LinkKind, LinkStatus, RequesterType
from core.repositories.boards import ColumnRepository
from core.repositories.cards import TagRepository
from core.services.cards import CardService
from core.services.suggestions import SuggestionService
from core.services.tags import TagService

mcp = new_server(
    "cards_mcp",
    "Read feature-request cards and record suggestions about them. Write tools create pending "
    "suggestions only; they never change a card.",
)


# ---- output models ----------------------------------------------------------------------------
class TagOut(BaseModel):
    id: int
    name: str
    color: str


class CardOut(BaseModel):
    id: int
    board_id: int
    column_id: int
    column: str = Field(description="Name of the status column the card is in")
    position: int
    title: str
    description: str
    requester_type: RequesterType
    progress: int
    priority_score: float | None
    version: int
    tags: list[TagOut]
    vote_count: int
    comment_count: int
    created_at: datetime
    updated_at: datetime


class CommentOut(BaseModel):
    id: int
    author: str
    body: str
    created_at: datetime


class FeedbackOut(BaseModel):
    id: int
    source: str
    quote: str
    customer_ref: str | None


class LinkOut(BaseModel):
    card_id: int
    title: str
    kind: LinkKind
    status: LinkStatus
    confidence: float | None


class ProblemOut(BaseModel):
    id: int
    title: str
    statement: str
    card_count: int


class AttachedProblemOut(BaseModel):
    id: int
    title: str
    status: LinkStatus
    confidence: float | None


class CardContextOut(BaseModel):
    card: CardOut
    comments: list[CommentOut]
    feedback: list[FeedbackOut]
    links: list[LinkOut]
    problems: list[AttachedProblemOut]
    pending_suggestions: int


class ListCardsResult(BaseModel):
    cards: list[CardOut]
    has_more: bool = Field(description="True when more cards match than were returned")


class LinkResult(BaseModel):
    link_id: int
    card_id: int = Field(description="Lower card id of the pair (links are stored once per pair)")
    other_card_id: int
    kind: LinkKind
    status: LinkStatus
    confidence: float | None
    created: bool = Field(description="False when the pair was already linked; a suggestion is then refreshed")


class ProblemAttachResult(BaseModel):
    card_id: int
    problem_id: int
    status: LinkStatus
    confidence: float | None
    created: bool


class ProblemsResult(BaseModel):
    problems: list[ProblemOut]


class TagsResult(BaseModel):
    tags: list[TagOut]


# ---- input models -----------------------------------------------------------------------------
class CardFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column_id: int | None = Field(None, description="Only cards in this column")
    column: str | None = Field(None, description="Only cards in the column with this name, e.g. 'Inbox'")
    tag: str | None = Field(None, description="Only cards with this tag name")
    requester_type: RequesterType | None = None
    query: str | None = Field(None, max_length=200, description="Text to find in title or description")
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class CardFieldsProposal(BaseModel):
    """Fields to propose for a card; set at least one."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    description: Annotated[str, StringConstraints(max_length=10000)] | None = None
    requester_type: RequesterType | None = None
    progress: int | None = Field(None, ge=0, le=100)
    priority_score: float | None = Field(None, ge=0, le=100, description="null clears the score")

    @model_validator(mode="after")
    def _something_set(self) -> CardFieldsProposal:
        if not self.model_fields_set:
            raise ValueError("Propose at least one field")
        return self


# ---- helpers ----------------------------------------------------------------------------------
def _card_out(session, card: Card) -> CardOut:
    column = ColumnRepository(session).get(card.column_id)
    return CardOut(
        id=card.id,
        board_id=card.board_id,
        column_id=card.column_id,
        column=column.name if column else "",
        position=card.position,
        title=card.title,
        description=card.description,
        requester_type=card.requester_type,
        progress=card.progress,
        priority_score=card.priority_score,
        version=card.version,
        tags=[TagOut(id=t.id, name=t.name, color=t.color) for t in card.tags],
        vote_count=card.vote_count,
        comment_count=card.comment_count,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


def _suggestion(draft, created: bool) -> SuggestionOut:
    return SuggestionOut(draft_id=draft.id, card_id=draft.card_id, kind=draft.kind, created=created)


# ---- read tools -------------------------------------------------------------------------------
@mcp.tool()
def get_card(card_id: CardId) -> CardOut:
    """Get one card: title, description, column, tags, votes and comment count. Read-only."""
    with session_scope() as session:
        return _card_out(session, CardService(session).get_card(card_id))


@mcp.tool()
def list_cards(
    board_id: Annotated[int, Field(ge=1, description="Board id")],
    filters: Annotated[
        CardFilters | None, Field(description="Optional filters; omit to list every card on the board")
    ] = None,
) -> ListCardsResult:
    """List cards on a board in board order, optionally filtered by column, tag, requester type or text. Read-only."""
    f = filters or CardFilters()
    with session_scope() as session:
        service = CardService(session)
        if service.boards.get(board_id) is None:
            raise NotFoundError(f"Board {board_id} not found")
        column_id = f.column_id
        if f.column is not None:
            column = ColumnRepository(session).by_name(board_id, f.column)
            if column is None:
                return ListCardsResult(cards=[], has_more=False)
            column_id = column.id
        tag_id = None
        if f.tag is not None:
            tag = TagRepository(session).by_name(board_id, f.tag)
            if tag is None:
                return ListCardsResult(cards=[], has_more=False)
            tag_id = tag.id
        cards = service.search(
            board_id=board_id,
            column_id=column_id,
            tag_id=tag_id,
            requester_type=f.requester_type,
            q=f.query,
            limit=f.limit + 1,
            offset=f.offset,
        )
        return ListCardsResult(cards=[_card_out(session, c) for c in cards[: f.limit]], has_more=len(cards) > f.limit)


@mcp.tool()
def get_card_context(card_id: CardId) -> CardContextOut:
    """Get a card with its comments, customer feedback quotes, related/duplicate links, attached problems and the number of pending AI suggestions. Read-only."""
    with session_scope() as session:
        detail = CardService(session).detail(card_id)
        pending = SuggestionService(session).list_drafts(card_id, status=DraftStatus.pending, limit=200)
        return CardContextOut(
            card=_card_out(session, detail.card),
            comments=[
                CommentOut(id=c.id, author=c.author.name, body=c.body, created_at=c.created_at) for c in detail.comments
            ],
            feedback=[
                FeedbackOut(id=f.id, source=f.source, quote=f.quote, customer_ref=f.customer_ref)
                for f in detail.feedback
            ],
            links=[
                LinkOut(
                    card_id=other.id, title=other.title, kind=link.kind, status=link.status, confidence=link.confidence
                )
                for link, other in detail.links
            ],
            problems=[
                AttachedProblemOut(id=p.id, title=p.title, status=cp.status, confidence=cp.confidence)
                for cp, p in detail.problems
            ],
            pending_suggestions=len(pending),
        )


@mcp.tool()
def list_problems(board_id: Annotated[int, Field(ge=1, description="Board id")]) -> ProblemsResult:
    """List the underlying customer problems defined on a board, with how many cards are attached to each. Read-only."""
    with session_scope() as session:
        if CardService(session).boards.get(board_id) is None:
            raise NotFoundError(f"Board {board_id} not found")
        return ProblemsResult(
            problems=[
                ProblemOut(id=p.id, title=p.title, statement=p.statement, card_count=count)
                for p, count in SuggestionService(session).list_problems(board_id)
            ]
        )


@mcp.tool()
def list_tags(board_id: Annotated[int, Field(ge=1, description="Board id")]) -> TagsResult:
    """List the tags defined on a board (id, name, colour), ordered by name. Read-only."""
    with session_scope() as session:
        return TagsResult(
            tags=[TagOut(id=t.id, name=t.name, color=t.color) for t in TagService(session).list_tags(board_id)]
        )


# ---- suggestion tools (none of these changes the card) ----------------------------------------
@mcp.tool()
def update_card_fields(
    card_id: CardId,
    fields: Annotated[CardFieldsProposal, Field(description="The fields to propose new values for; set at least one")],
    rationale: Rationale,
    confidence: Confidence | None = None,
    run_id: RunId = None,
) -> SuggestionOut:
    """PROPOSE new values for card fields (title, description, requester type, progress, priority score). Creates a pending suggestion; the card is NOT changed until a person accepts it."""
    with session_scope() as session:
        card = CardService(session).get_card(card_id)
        payload = {
            "fields": fields.model_dump(mode="json", exclude_unset=True),
            "base_version": card.version,
            "rationale": rationale,
        }
        draft, created = SuggestionService(session).save_draft(
            card_id, DraftKind.field_edit, payload, confidence=confidence, run_id=run_id
        )
        return _suggestion(draft, created)


@mcp.tool()
def add_comment(
    card_id: CardId,
    body: Annotated[str, Field(min_length=1, max_length=5000, description="Comment text")],
    confidence: Confidence | None = None,
    run_id: RunId = None,
) -> SuggestionOut:
    """PROPOSE a comment on a card. Creates a pending suggestion; no comment is posted until a person accepts it."""
    with session_scope() as session:
        CardService(session).get_card(card_id)
        draft, created = SuggestionService(session).save_draft(
            card_id, DraftKind.comment, {"body": body.strip()}, confidence=confidence, run_id=run_id
        )
        return _suggestion(draft, created)


@mcp.tool()
def add_tag(
    card_id: CardId,
    tag: Annotated[
        str, Field(min_length=1, max_length=40, description="Tag name; an existing board tag is matched ignoring case")
    ],
    reason: Rationale,
    confidence: Confidence | None = None,
    run_id: RunId = None,
) -> SuggestionOut:
    """PROPOSE tagging a card. Creates a pending suggestion; the tag is NOT added until a person accepts it. Fails if the card already has the tag."""
    with session_scope() as session:
        card = CardService(session).get_card(card_id)
        name = tag.strip()
        if not name:
            raise InvalidInputError("Tag name cannot be empty", code="invalid_tag")
        existing = TagRepository(session).by_name(card.board_id, name)
        if existing is not None and any(t.id == existing.id for t in card.tags):
            raise ConflictError(f"Card {card_id} already has the tag '{existing.name}'", code="already_tagged")
        payload = {
            "name": existing.name if existing else name,
            "tag_id": existing.id if existing else None,
            "is_existing": existing is not None,
            "reason": reason,
        }
        draft, created = SuggestionService(session).save_draft(
            card_id, DraftKind.tag, payload, confidence=confidence, run_id=run_id
        )
        return _suggestion(draft, created)


@mcp.tool()
def link_cards(
    card_id: CardId,
    other_card_id: CardId,
    type: Annotated[
        Literal["duplicate", "related"], Field(description="duplicate = same need; related = same area, different need")
    ],
    confidence: Confidence,
    rationale: Rationale,
    status: Annotated[
        Literal["suggested"],
        Field(description="Only 'suggested' is accepted: confirming a link is a human action in the app"),
    ] = "suggested",
) -> LinkResult:
    """PROPOSE that two cards on the same board are duplicates or related. Stored once per pair with status 'suggested'; asking again refreshes the suggestion; a link a person confirmed is never changed."""
    with session_scope() as session:
        link, created = SuggestionService(session).suggest_link(
            card_id, other_card_id, LinkKind(type), confidence=confidence, rationale=rationale
        )
        return LinkResult(
            link_id=link.id,
            card_id=link.card_id,
            other_card_id=link.other_card_id,
            kind=link.kind,
            status=link.status,
            confidence=link.confidence,
            created=created,
        )


@mcp.tool()
def attach_problem(
    card_id: CardId,
    problem_id: Annotated[
        int, Field(ge=1, description="Id of an existing problem on the same board (see list_problems)")
    ],
    confidence: Confidence,
    rationale: Rationale,
    status: Annotated[
        Literal["suggested"],
        Field(description="Only 'suggested' is accepted: confirming is a human action in the app"),
    ] = "suggested",
) -> ProblemAttachResult:
    """PROPOSE that a card belongs to an existing underlying problem. Stored with status 'suggested'. To propose a brand-new problem use save_suggestion with kind 'problem'."""
    with session_scope() as session:
        attachment, created = SuggestionService(session).suggest_problem(
            card_id, problem_id, confidence=confidence, rationale=rationale
        )
        return ProblemAttachResult(
            card_id=attachment.card_id,
            problem_id=attachment.problem_id,
            status=attachment.status,
            confidence=attachment.confidence,
            created=created,
        )


@mcp.tool()
def save_suggestion(
    card_id: CardId,
    kind: Annotated[DraftKind, Field(description="What the suggestion is about")],
    payload: Annotated[dict[str, Any], Field(description="The proposal itself, as a JSON object")],
    confidence: Confidence | None = None,
    run_id: RunId = None,
) -> SuggestionOut:
    """Store any AI proposal (tag, comment, link, problem, score, brief, comms, field_edit) as a pending suggestion for a person to review. Identical pending suggestions are not duplicated."""
    if not payload:
        raise InvalidInputError("payload must not be empty", code="empty_payload")
    if len(str(payload)) > 20_000:
        raise InvalidInputError("payload is too large (20,000 characters max)", code="payload_too_large")
    with session_scope() as session:
        draft, created = SuggestionService(session).save_draft(
            card_id, kind, payload, confidence=confidence, run_id=run_id
        )
        return _suggestion(draft, created)
