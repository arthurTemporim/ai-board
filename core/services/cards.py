from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from core.errors import ConflictError, InvalidInputError, NotFoundError
from core.models import (
    Card,
    CardLink,
    CardProblem,
    Comment,
    Feedback,
    Problem,
    RequesterType,
    Score,
    Tag,
)
from core.models.types import utcnow
from core.repositories.boards import BoardRepository, ColumnRepository
from core.repositories.cards import CardRepository, TagRepository
from core.services.ordering import place, renumber

UPDATABLE_FIELDS = {"title", "description", "requester_type", "progress", "priority_score"}


@dataclass
class CardDetail:
    card: Card
    comments: list[Comment] = field(default_factory=list)
    feedback: list[Feedback] = field(default_factory=list)
    links: list[tuple[CardLink, Card]] = field(default_factory=list)
    problems: list[tuple[CardProblem, Problem]] = field(default_factory=list)
    latest_score: Score | None = None


class CardService:
    def __init__(self, session: Session):
        self.session = session
        self.cards = CardRepository(session)
        self.columns = ColumnRepository(session)
        self.boards = BoardRepository(session)
        self.tags = TagRepository(session)

    def get_card(self, card_id: int) -> Card:
        card = self.cards.get(card_id)
        if card is None:
            raise NotFoundError(f"Card {card_id} not found")
        return card

    def detail(self, card_id: int) -> CardDetail:
        card = self.get_card(card_id)
        return CardDetail(
            card=card,
            comments=self.cards.comments(card_id),
            feedback=self.cards.feedback(card_id),
            links=self.cards.links(card_id),
            problems=self.cards.problems(card_id),
            latest_score=self.cards.latest_score(card_id),
        )

    def search(self, **filters) -> list[Card]:
        return self.cards.search(**filters)

    def create_card(
        self,
        *,
        board_id: int,
        title: str,
        description: str = "",
        requester_type: RequesterType = RequesterType.unknown,
        progress: int = 0,
        column_id: int | None = None,
        position: int | None = None,
        tag_ids: list[int] | None = None,
        created_by_id: int | None = None,
    ) -> Card:
        if self.boards.get(board_id) is None:
            raise NotFoundError(f"Board {board_id} not found")
        if column_id is None:
            columns = self.columns.for_board(board_id)
            if not columns:
                raise InvalidInputError("Board has no columns to put the card in")
            column = columns[0]  # first status column, normally Inbox
        else:
            column = self.columns.get(column_id)
            if column is None:
                raise NotFoundError(f"Column {column_id} not found")
            if column.board_id != board_id:
                raise InvalidInputError("Column does not belong to this board", code="wrong_board")
        tags = self._board_tags(board_id, tag_ids or [])

        siblings = self.cards.in_column(column.id)
        now = utcnow()
        card = self.cards.add(
            Card(
                board_id=board_id,
                column_id=column.id,
                position=len(siblings),
                title=title,
                description=description,
                requester_type=requester_type,
                progress=progress,
                created_by_id=created_by_id,
                created_at=now,
                updated_at=now,
                tags=tags,
            )
        )
        if position is not None:
            renumber(place(siblings + [card], card, position))
        self.session.flush()
        self.session.refresh(card)
        return card

    def update_card(self, card_id: int, changes: dict, expected_version: int | None = None) -> Card:
        card = self.get_card(card_id)
        self._check_version(card, expected_version)
        changes = dict(changes)
        if "tag_ids" in changes:
            card.tags = self._board_tags(card.board_id, changes.pop("tag_ids"))
        for name, value in changes.items():
            if name not in UPDATABLE_FIELDS:
                raise InvalidInputError(f"Field '{name}' cannot be updated", code="unknown_field")
            setattr(card, name, value)
        self._touch(card)
        self.session.flush()
        self.session.refresh(card)
        return card

    def delete_card(self, card_id: int) -> None:
        card = self.get_card(card_id)
        column_id = card.column_id
        self.cards.delete(card)
        renumber(self.cards.in_column(column_id))
        self.session.flush()

    def move_card(
        self, card_id: int, column_id: int, position: int, expected_version: int | None = None
    ) -> Card:
        """Move a card to `position` in `column_id`, renumbering both columns in one transaction.

        All validation happens before any row is changed; the caller's session commits (or rolls
        back) everything together.
        """
        card = self.get_card(card_id)
        self._check_version(card, expected_version)
        target = self.columns.get(column_id)
        if target is None:
            raise NotFoundError(f"Column {column_id} not found")
        if target.board_id != card.board_id:
            raise InvalidInputError(
                "Cannot move a card to a column of another board", code="wrong_board"
            )

        source_id = card.column_id
        target_cards = place(self.cards.in_column(column_id), card, position)
        source_cards = (
            [c for c in self.cards.in_column(source_id) if c.id != card.id]
            if source_id != column_id
            else []
        )

        card.column_id = column_id
        renumber(target_cards)
        renumber(source_cards)
        self._touch(card)
        self.session.flush()
        self.session.refresh(card)
        return card

    # ---- helpers ------------------------------------------------------------------------
    def _board_tags(self, board_id: int, tag_ids: list[int]) -> list[Tag]:
        tags = self.tags.many(tag_ids)
        missing = set(tag_ids) - {t.id for t in tags}
        if missing:
            raise NotFoundError(f"Tag(s) not found: {sorted(missing)}")
        if any(t.board_id != board_id for t in tags):
            raise InvalidInputError("Tag does not belong to this board", code="wrong_board")
        return tags

    @staticmethod
    def _check_version(card: Card, expected: int | None) -> None:
        if expected is not None and expected != card.version:
            raise ConflictError(
                f"Card was modified by someone else (your version {expected}, current {card.version})",
                code="version_conflict",
            )

    @staticmethod
    def _touch(card: Card) -> None:
        card.version += 1
        card.updated_at = utcnow()
