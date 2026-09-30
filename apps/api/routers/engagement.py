"""Comments, votes and feedback entries, all scoped to a card."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from apps.api.deps import ActorDep, OptionalActorDep, SessionDep
from apps.api.schemas import (
    CommentCreate,
    CommentRead,
    CommentUpdate,
    FeedbackCreate,
    FeedbackRead,
    FeedbackUpdate,
    VoteSummaryRead,
)
from core.services.engagement import CommentService, FeedbackService, VoteService

router = APIRouter()


# ---- comments ---------------------------------------------------------------------------
@router.get("/cards/{card_id}/comments", response_model=list[CommentRead], tags=["comments"])
def list_comments(card_id: int, session: SessionDep):
    return CommentService(session).list_comments(card_id)


@router.post(
    "/cards/{card_id}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
    tags=["comments"],
)
def add_comment(card_id: int, body: CommentCreate, session: SessionDep, actor: ActorDep):
    return CommentService(session).add_comment(card_id, actor.id, body.body)


@router.patch("/comments/{comment_id}", response_model=CommentRead, tags=["comments"])
def update_comment(comment_id: int, body: CommentUpdate, session: SessionDep, actor: ActorDep):
    return CommentService(session).update_comment(comment_id, actor.id, body.body)


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["comments"])
def delete_comment(comment_id: int, session: SessionDep, actor: ActorDep):
    CommentService(session).delete_comment(comment_id, actor.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- votes ------------------------------------------------------------------------------
@router.get("/cards/{card_id}/votes", response_model=VoteSummaryRead, tags=["votes"])
def get_votes(card_id: int, session: SessionDep, actor_id: OptionalActorDep):
    return VoteService(session).summary(card_id, actor_id)


@router.post(
    "/cards/{card_id}/votes",
    response_model=VoteSummaryRead,
    status_code=status.HTTP_201_CREATED,
    tags=["votes"],
    summary="Vote for a card (one vote per user; a second vote returns 409)",
)
def add_vote(card_id: int, session: SessionDep, actor: ActorDep):
    return VoteService(session).add_vote(card_id, actor.id)


@router.delete("/cards/{card_id}/votes", response_model=VoteSummaryRead, tags=["votes"])
def remove_vote(card_id: int, session: SessionDep, actor: ActorDep):
    return VoteService(session).remove_vote(card_id, actor.id)


# ---- feedback ---------------------------------------------------------------------------
@router.get("/cards/{card_id}/feedback", response_model=list[FeedbackRead], tags=["feedback"])
def list_feedback(card_id: int, session: SessionDep):
    return FeedbackService(session).list_feedback(card_id)


@router.post(
    "/cards/{card_id}/feedback",
    response_model=FeedbackRead,
    status_code=status.HTTP_201_CREATED,
    tags=["feedback"],
)
def add_feedback(card_id: int, body: FeedbackCreate, session: SessionDep):
    return FeedbackService(session).add_feedback(
        card_id, body.source, body.quote, body.customer_ref
    )


@router.patch("/feedback/{feedback_id}", response_model=FeedbackRead, tags=["feedback"])
def update_feedback(feedback_id: int, body: FeedbackUpdate, session: SessionDep):
    return FeedbackService(session).update_feedback(feedback_id, body.model_dump(exclude_unset=True))


@router.delete("/feedback/{feedback_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["feedback"])
def delete_feedback(feedback_id: int, session: SessionDep):
    FeedbackService(session).delete_feedback(feedback_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
