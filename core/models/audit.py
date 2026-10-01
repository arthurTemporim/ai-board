from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base
from core.models.types import UTCDateTime, utcnow


class AuditEntry(Base):
    """Append-only record of a human decision about an AI suggestion (and what it changed).

    `detail` holds what is needed to understand or undo the change: the suggestion, the values
    before and after, and any reason the person gave. Rows are never updated or deleted by the app.
    """

    __tablename__ = "audit_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("card.id", ondelete="SET NULL"), index=True, default=None)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), default=None)
    action: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
