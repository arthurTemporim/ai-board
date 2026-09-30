from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import Board, Card, Column, User


class UserRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def list(self) -> list[User]:
        return list(self.session.scalars(select(User).order_by(User.id)))


class BoardRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, board_id: int) -> Board | None:
        return self.session.get(Board, board_id)

    def list(self) -> list[Board]:
        return list(self.session.scalars(select(Board).order_by(Board.id)))

    def add(self, board: Board) -> Board:
        self.session.add(board)
        self.session.flush()
        return board

    def delete(self, board: Board) -> None:
        # Cards reference columns with RESTRICT, so remove them first.
        for card in self.session.scalars(select(Card).where(Card.board_id == board.id)):
            self.session.delete(card)
        self.session.flush()
        self.session.delete(board)
        self.session.flush()


class ColumnRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, column_id: int) -> Column | None:
        return self.session.get(Column, column_id)

    def for_board(self, board_id: int) -> list[Column]:
        return list(
            self.session.scalars(
                select(Column).where(Column.board_id == board_id).order_by(Column.position, Column.id)
            )
        )

    def by_name(self, board_id: int, name: str) -> Column | None:
        return self.session.scalar(
            select(Column).where(
                Column.board_id == board_id, func.lower(Column.name) == name.lower()
            )
        )

    def add(self, column: Column) -> Column:
        self.session.add(column)
        self.session.flush()
        return column

    def delete(self, column: Column) -> None:
        self.session.delete(column)
        self.session.flush()

    def card_count(self, column_id: int) -> int:
        return self.session.scalar(
            select(func.count(Card.id)).where(Card.column_id == column_id)
        ) or 0
