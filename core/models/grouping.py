from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, column_property, mapped_column

from core.db import Base
from core.models.card import Card
from core.models.enums import LinkKind, LinkStatus
from core.models.types import UTCDateTime, enum_column, utcnow


class CardLink(Base):
    """Related/duplicate relation between two cards, stored once with card_id < other_card_id."""

    __tablename__ = "card_link"
    __table_args__ = (
        CheckConstraint("card_id < other_card_id", name="ck_card_link_order"),
        UniqueConstraint("card_id", "other_card_id", name="uq_card_link_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"), index=True)
    other_card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"), index=True)
    kind: Mapped[LinkKind] = mapped_column(enum_column(LinkKind))
    status: Mapped[LinkStatus] = mapped_column(enum_column(LinkStatus), default=LinkStatus.suggested)
    # Set when an AI step proposed the link; null for links made by people.
    confidence: Mapped[float | None] = mapped_column(default=None)
    rationale: Mapped[str | None] = mapped_column(Text, default=None)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Problem(Base):
    """An underlying customer problem that several requests belong to."""

    __tablename__ = "problem"

    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("board.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    statement: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CardProblem(Base):
    __tablename__ = "card_problem"

    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"), primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problem.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[LinkStatus] = mapped_column(enum_column(LinkStatus), default=LinkStatus.suggested)
    confidence: Mapped[float | None] = mapped_column(default=None)
    rationale: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


def _link_count(kind: LinkKind):
    return column_property(
        select(func.count(CardLink.id))
        .where(
            (CardLink.card_id == Card.id) | (CardLink.other_card_id == Card.id),
            CardLink.kind == kind,
        )
        .correlate_except(CardLink)
        .scalar_subquery()
    )


# Drive the duplicate / related indicators on cards (suggested and confirmed links both count).
Card.duplicate_count = _link_count(LinkKind.duplicate)
Card.related_count = _link_count(LinkKind.related)
