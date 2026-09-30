from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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


class CardRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, card_id: int) -> Card | None:
        return self.session.get(Card, card_id)

    def add(self, card: Card) -> Card:
        self.session.add(card)
        self.session.flush()
        return card

    def delete(self, card: Card) -> None:
        self.session.delete(card)
        self.session.flush()

    def in_column(self, column_id: int) -> list[Card]:
        return list(
            self.session.scalars(
                select(Card).where(Card.column_id == column_id).order_by(Card.position, Card.id)
            )
        )

    def for_board(self, board_id: int) -> list[Card]:
        return list(
            self.session.scalars(
                select(Card).where(Card.board_id == board_id).order_by(Card.position, Card.id)
            )
        )

    def search(
        self,
        *,
        board_id: int | None = None,
        column_id: int | None = None,
        tag_id: int | None = None,
        requester_type: RequesterType | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Card]:
        stmt = select(Card)
        if board_id is not None:
            stmt = stmt.where(Card.board_id == board_id)
        if column_id is not None:
            stmt = stmt.where(Card.column_id == column_id)
        if tag_id is not None:
            stmt = stmt.where(Card.tags.any(Tag.id == tag_id))
        if requester_type is not None:
            stmt = stmt.where(Card.requester_type == requester_type)
        if q:
            stmt = stmt.where(Card.title.icontains(q, autoescape=True) | Card.description.icontains(q, autoescape=True))
        stmt = stmt.order_by(Card.board_id, Card.column_id, Card.position, Card.id)
        return list(self.session.scalars(stmt.limit(limit).offset(offset)))

    def comments(self, card_id: int) -> list[Comment]:
        return list(
            self.session.scalars(
                select(Comment).where(Comment.card_id == card_id).order_by(Comment.created_at, Comment.id)
            )
        )

    def feedback(self, card_id: int) -> list[Feedback]:
        return list(
            self.session.scalars(
                select(Feedback)
                .where(Feedback.card_id == card_id)
                .order_by(Feedback.created_at, Feedback.id)
            )
        )

    def links(self, card_id: int) -> list[tuple[CardLink, Card]]:
        """Links touching the card, each with the card on the other side."""
        links = self.session.scalars(
            select(CardLink).where((CardLink.card_id == card_id) | (CardLink.other_card_id == card_id))
        ).all()
        result = []
        for link in links:
            other_id = link.other_card_id if link.card_id == card_id else link.card_id
            other = self.get(other_id)
            if other is not None:
                result.append((link, other))
        return result

    def problems(self, card_id: int) -> list[tuple[CardProblem, Problem]]:
        rows = self.session.execute(
            select(CardProblem, Problem)
            .join(Problem, Problem.id == CardProblem.problem_id)
            .where(CardProblem.card_id == card_id)
            .order_by(Problem.id)
        )
        return [(cp, problem) for cp, problem in rows]

    def latest_score(self, card_id: int) -> Score | None:
        return self.session.scalar(
            select(Score).where(Score.card_id == card_id).order_by(Score.created_at.desc(), Score.id.desc())
        )


class TagRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, tag_id: int) -> Tag | None:
        return self.session.get(Tag, tag_id)

    def many(self, tag_ids: Sequence[int]) -> list[Tag]:
        if not tag_ids:
            return []
        return list(self.session.scalars(select(Tag).where(Tag.id.in_(set(tag_ids)))))

    def for_board(self, board_id: int) -> list[Tag]:
        return list(self.session.scalars(select(Tag).where(Tag.board_id == board_id).order_by(Tag.name)))

    def by_name(self, board_id: int, name: str) -> Tag | None:
        return self.session.scalar(
            select(Tag).where(Tag.board_id == board_id, func.lower(Tag.name) == name.lower())
        )

    def add(self, tag: Tag) -> Tag:
        self.session.add(tag)
        self.session.flush()
        return tag

    def delete(self, tag: Tag) -> None:
        self.session.delete(tag)
        self.session.flush()
