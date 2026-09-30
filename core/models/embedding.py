from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base
from core.models.types import UTCDateTime, utcnow


class CardEmbedding(Base):
    """Latest text embedding of a card: float32 values, L2-normalised, packed into `vector`."""

    __tablename__ = "card_embedding"

    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(120))
    dim: Mapped[int]
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    text_hash: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
