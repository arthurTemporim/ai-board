from __future__ import annotations

from fastapi import APIRouter, Response, status

from apps.api.deps import SessionDep
from apps.api.schemas import TagCreate, TagRead, TagUpdate
from core.services.tags import TagService

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagRead])
def list_tags(board_id: int, session: SessionDep):
    return TagService(session).list_tags(board_id)


@router.post("", response_model=TagRead, status_code=status.HTTP_201_CREATED)
def create_tag(body: TagCreate, session: SessionDep):
    return TagService(session).create_tag(body.board_id, body.name, body.color)


@router.patch("/{tag_id}", response_model=TagRead)
def update_tag(tag_id: int, body: TagUpdate, session: SessionDep):
    return TagService(session).update_tag(tag_id, body.model_dump(exclude_unset=True))


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(tag_id: int, session: SessionDep):
    TagService(session).delete_tag(tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
