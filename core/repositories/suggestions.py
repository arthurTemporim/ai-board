from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import (
    AIRun,
    Card,
    CardEmbedding,
    CardLink,
    CardProblem,
    Draft,
    DraftKind,
    DraftStatus,
    Problem,
    Score,
)


class DraftRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, draft: Draft) -> Draft:
        self.session.add(draft)
        self.session.flush()
        return draft

    def list(
        self, card_id: int, kind: DraftKind | None = None, status: DraftStatus | None = None, limit: int = 50
    ) -> list[Draft]:
        stmt = select(Draft).where(Draft.card_id == card_id)
        if kind is not None:
            stmt = stmt.where(Draft.kind == kind)
        if status is not None:
            stmt = stmt.where(Draft.status == status)
        return list(self.session.scalars(stmt.order_by(Draft.id.desc()).limit(limit)))

    def run_exists(self, run_id: int) -> bool:
        return self.session.get(AIRun, run_id) is not None


class LinkRepository:
    def __init__(self, session: Session):
        self.session = session

    def pair(self, low: int, high: int) -> CardLink | None:
        return self.session.scalar(select(CardLink).where(CardLink.card_id == low, CardLink.other_card_id == high))

    def add(self, link: CardLink) -> CardLink:
        self.session.add(link)
        self.session.flush()
        return link


class ProblemRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, problem_id: int) -> Problem | None:
        return self.session.get(Problem, problem_id)

    def for_board(self, board_id: int) -> list[tuple[Problem, int]]:
        """Problems with the number of cards attached to each."""
        rows = self.session.execute(
            select(Problem, func.count(CardProblem.card_id))
            .outerjoin(CardProblem, CardProblem.problem_id == Problem.id)
            .where(Problem.board_id == board_id)
            .group_by(Problem.id)
            .order_by(Problem.id)
        )
        return [(problem, count) for problem, count in rows]

    def attachment(self, card_id: int, problem_id: int) -> CardProblem | None:
        return self.session.get(CardProblem, (card_id, problem_id))

    def add_attachment(self, attachment: CardProblem) -> CardProblem:
        self.session.add(attachment)
        self.session.flush()
        return attachment


class ScoreRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, score: Score) -> Score:
        self.session.add(score)
        self.session.flush()
        return score

    def latest(self, card_id: int) -> Score | None:
        return self.session.scalar(
            select(Score).where(Score.card_id == card_id).order_by(Score.created_at.desc(), Score.id.desc())
        )


class EmbeddingRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, card_id: int) -> CardEmbedding | None:
        return self.session.get(CardEmbedding, card_id)

    def upsert(self, card_id: int, model: str, dim: int, vector: bytes, text_hash: str) -> CardEmbedding:
        row = self.get(card_id)
        if row is None:
            row = CardEmbedding(card_id=card_id)
            self.session.add(row)
        row.model, row.dim, row.vector, row.text_hash = model, dim, vector, text_hash
        self.session.flush()
        return row

    def for_board(self, board_id: int, model: str) -> list[CardEmbedding]:
        return list(
            self.session.scalars(
                select(CardEmbedding)
                .join(Card, Card.id == CardEmbedding.card_id)
                .where(Card.board_id == board_id, CardEmbedding.model == model)
                .order_by(CardEmbedding.card_id)
            )
        )

    def count_cards_without(self, board_id: int, model: str) -> int:
        """Cards on the board with no embedding for `model`."""
        indexed = select(CardEmbedding.card_id).where(CardEmbedding.model == model)
        return (
            self.session.scalar(select(func.count(Card.id)).where(Card.board_id == board_id, Card.id.not_in(indexed)))
            or 0
        )
