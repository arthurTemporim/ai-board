from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base
from core.models.enums import RequesterType
from core.models.types import UTCDateTime, enum_column, utcnow


class Tag(Base):
    __tablename__ = "tag"
    __table_args__ = (UniqueConstraint("board_id", "name", name="uq_tag_board_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("board.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    color: Mapped[str] = mapped_column(String(7), default="#6B7280")


class CardTag(Base):
    __tablename__ = "card_tag"

    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True)


class Card(Base):
    __tablename__ = "card"
    __table_args__ = (
        CheckConstraint("progress BETWEEN 0 AND 100", name="ck_card_progress"),
        Index("ix_card_column_position", "column_id", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("board.id", ondelete="CASCADE"), index=True)
    column_id: Mapped[int] = mapped_column(ForeignKey("board_column.id", ondelete="RESTRICT"))
    position: Mapped[int] = mapped_column(default=0)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    requester_type: Mapped[RequesterType] = mapped_column(
        enum_column(RequesterType), default=RequesterType.unknown
    )
    progress: Mapped[int] = mapped_column(default=0)
    priority_score: Mapped[float | None] = mapped_column(default=None)
    # Optimistic concurrency: bumped on every edit or move of this card.
    version: Mapped[int] = mapped_column(default=1)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    tags: Mapped[list[Tag]] = relationship(
        secondary="card_tag", order_by="Tag.name", lazy="selectin"
    )
    # vote_count and comment_count are added in core.models.engagement as read-only subqueries.
