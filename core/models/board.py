from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from core.models.types import UTCDateTime, utcnow


class Board(Base):
    __tablename__ = "board"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    columns: Mapped[list[Column]] = relationship(
        back_populates="board",
        order_by="Column.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Column(Base):
    __tablename__ = "board_column"
    __table_args__ = (UniqueConstraint("board_id", "name", name="uq_column_board_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("board.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(default=0)
    wip_limit: Mapped[int | None] = mapped_column(default=None)

    board: Mapped[Board] = relationship(back_populates="columns")
