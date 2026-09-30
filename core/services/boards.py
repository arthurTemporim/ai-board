from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.errors import ConflictError, NotFoundError
from core.models import Board, Card, Column
from core.repositories.boards import BoardRepository, ColumnRepository
from core.repositories.cards import CardRepository
from core.services.ordering import place, renumber

DEFAULT_COLUMNS = [
    "Inbox",
    "Triaged",
    "Under Review",
    "Planned",
    "In Progress",
    "Shipped",
    "Declined",
]


@dataclass
class BoardView:
    board: Board
    columns: list[tuple[Column, list[Card]]]


class BoardService:
    def __init__(self, session: Session):
        self.session = session
        self.boards = BoardRepository(session)
        self.columns = ColumnRepository(session)
        self.cards = CardRepository(session)

    # ---- boards -------------------------------------------------------------------------
    def list_boards(self) -> list[Board]:
        return self.boards.list()

    def get_board(self, board_id: int) -> Board:
        board = self.boards.get(board_id)
        if board is None:
            raise NotFoundError(f"Board {board_id} not found")
        return board

    def create_board(
        self, name: str, description: str = "", column_names: list[str] | None = None
    ) -> Board:
        names = column_names if column_names is not None else DEFAULT_COLUMNS
        board = self.boards.add(Board(name=name, description=description))
        for position, column_name in enumerate(names):
            self.columns.add(Column(board_id=board.id, name=column_name, position=position))
        self.session.refresh(board)
        return board

    def update_board(self, board_id: int, changes: dict) -> Board:
        board = self.get_board(board_id)
        for field, value in changes.items():
            setattr(board, field, value)
        self.session.flush()
        return board

    def delete_board(self, board_id: int) -> None:
        self.boards.delete(self.get_board(board_id))

    def board_view(self, board_id: int) -> BoardView:
        board = self.get_board(board_id)
        by_column: dict[int, list[Card]] = {}
        for card in self.cards.for_board(board_id):
            by_column.setdefault(card.column_id, []).append(card)
        columns = self.columns.for_board(board_id)
        return BoardView(board, [(c, by_column.get(c.id, [])) for c in columns])

    # ---- columns ------------------------------------------------------------------------
    def get_column(self, column_id: int) -> Column:
        column = self.columns.get(column_id)
        if column is None:
            raise NotFoundError(f"Column {column_id} not found")
        return column

    def add_column(
        self, board_id: int, name: str, wip_limit: int | None = None, position: int | None = None
    ) -> Column:
        self.get_board(board_id)
        self._ensure_unique_name(board_id, name)
        existing = self.columns.for_board(board_id)
        column = self.columns.add(
            Column(board_id=board_id, name=name, wip_limit=wip_limit, position=len(existing))
        )
        if position is not None:
            renumber(place(existing + [column], column, position))
            self.session.flush()
        return column

    def update_column(self, column_id: int, changes: dict) -> Column:
        column = self.get_column(column_id)
        changes = dict(changes)
        position = changes.pop("position", None)
        if "name" in changes and changes["name"].lower() != column.name.lower():
            self._ensure_unique_name(column.board_id, changes["name"])
        for field, value in changes.items():
            setattr(column, field, value)
        if position is not None:
            renumber(place(self.columns.for_board(column.board_id), column, position))
        self.session.flush()
        return column

    def delete_column(self, column_id: int) -> None:
        column = self.get_column(column_id)
        count = self.columns.card_count(column_id)
        if count:
            raise ConflictError(
                f"Column '{column.name}' still has {count} card(s); move or delete them first",
                code="column_not_empty",
            )
        board_id = column.board_id
        self.columns.delete(column)
        renumber(self.columns.for_board(board_id))
        self.session.flush()

    def _ensure_unique_name(self, board_id: int, name: str) -> None:
        if self.columns.by_name(board_id, name) is not None:
            raise ConflictError(
                f"A column named '{name}' already exists on this board", code="duplicate_column"
            )
