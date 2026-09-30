from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from core.db import Base
from core.models.card import Card
from core.models.types import UTCDateTime, utcnow
from core.models.user import User


class Vote(Base):
    __tablename__ = "vote"
    __table_args__ = (UniqueConstraint("card_id", "user_id", name="uq_vote_card_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Comment(Base):
    __tablename__ = "comment"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    author: Mapped[User] = relationship(lazy="joined")


class Feedback(Base):
    """A customer feedback entry (quote) attached to a card."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(80))
    quote: Mapped[str] = mapped_column(Text)
    customer_ref: Mapped[str | None] = mapped_column(String(120), default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# Popularity and discussion size, computed in the same SELECT as the card (no N+1).
# After adding or removing a vote/comment, expire these attributes on the card before reading.
Card.vote_count = column_property(
    select(func.count(Vote.id)).where(Vote.card_id == Card.id).correlate_except(Vote).scalar_subquery()
)
Card.comment_count = column_property(
    select(func.count(Comment.id))
    .where(Comment.card_id == Card.id)
    .correlate_except(Comment)
    .scalar_subquery()
)
