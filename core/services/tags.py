from __future__ import annotations

from sqlalchemy.orm import Session

from core.errors import ConflictError, NotFoundError
from core.models import Tag
from core.repositories.boards import BoardRepository
from core.repositories.cards import TagRepository


class TagService:
    def __init__(self, session: Session):
        self.session = session
        self.tags = TagRepository(session)
        self.boards = BoardRepository(session)

    def get_tag(self, tag_id: int) -> Tag:
        tag = self.tags.get(tag_id)
        if tag is None:
            raise NotFoundError(f"Tag {tag_id} not found")
        return tag

    def list_tags(self, board_id: int) -> list[Tag]:
        if self.boards.get(board_id) is None:
            raise NotFoundError(f"Board {board_id} not found")
        return self.tags.for_board(board_id)

    def create_tag(self, board_id: int, name: str, color: str) -> Tag:
        if self.boards.get(board_id) is None:
            raise NotFoundError(f"Board {board_id} not found")
        self._ensure_unique(board_id, name)
        return self.tags.add(Tag(board_id=board_id, name=name, color=color))

    def update_tag(self, tag_id: int, changes: dict) -> Tag:
        tag = self.get_tag(tag_id)
        if "name" in changes and changes["name"].lower() != tag.name.lower():
            self._ensure_unique(tag.board_id, changes["name"])
        for field, value in changes.items():
            setattr(tag, field, value)
        self.session.flush()
        return tag

    def delete_tag(self, tag_id: int) -> None:
        self.tags.delete(self.get_tag(tag_id))

    def _ensure_unique(self, board_id: int, name: str) -> None:
        if self.tags.by_name(board_id, name) is not None:
            raise ConflictError(f"Tag '{name}' already exists on this board", code="duplicate_tag")
