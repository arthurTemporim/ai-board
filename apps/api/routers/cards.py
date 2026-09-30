from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from apps.api.deps import OptionalActorDep, SessionDep
from apps.api.schemas import (
    CardCreate,
    CardDetail,
    CardMove,
    CardRead,
    CardUpdate,
    LinkRead,
    ProblemRead,
)
from core.models import RequesterType
from core.services.cards import CardService

router = APIRouter(prefix="/cards", tags=["cards"])


@router.get("", response_model=list[CardRead], summary="List and search cards")
def list_cards(
    session: SessionDep,
    board_id: int | None = None,
    column_id: int | None = None,
    tag_id: int | None = None,
    requester_type: RequesterType | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Search title and description")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return CardService(session).search(
        board_id=board_id,
        column_id=column_id,
        tag_id=tag_id,
        requester_type=requester_type,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=CardRead, status_code=status.HTTP_201_CREATED)
def create_card(body: CardCreate, session: SessionDep, actor_id: OptionalActorDep):
    return CardService(session).create_card(created_by_id=actor_id, **body.model_dump())


@router.get("/{card_id}", response_model=CardDetail)
def get_card(card_id: int, session: SessionDep):
    detail = CardService(session).detail(card_id)
    return {
        **CardRead.model_validate(detail.card).model_dump(),
        "comments": detail.comments,
        "feedback": detail.feedback,
        "links": [
            LinkRead(card_id=other.id, title=other.title, kind=link.kind, status=link.status)
            for link, other in detail.links
        ],
        "problems": [
            ProblemRead(id=problem.id, title=problem.title, status=cp.status)
            for cp, problem in detail.problems
        ],
        "latest_score": detail.latest_score,
    }


@router.patch("/{card_id}", response_model=CardRead)
def update_card(card_id: int, body: CardUpdate, session: SessionDep):
    changes = body.model_dump(exclude_unset=True)
    version = changes.pop("version", None)
    return CardService(session).update_card(card_id, changes, expected_version=version)


@router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_card(card_id: int, session: SessionDep):
    CardService(session).delete_card(card_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{card_id}/move",
    response_model=CardRead,
    summary="Move or reorder a card",
    description="Sets the card's column and zero-based position atomically and renumbers "
    "the affected columns. Also used to reorder within a column.",
)
def move_card(card_id: int, body: CardMove, session: SessionDep):
    return CardService(session).move_card(
        card_id, body.column_id, body.position, expected_version=body.version
    )
