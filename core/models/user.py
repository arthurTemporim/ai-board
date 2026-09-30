from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class User(Base):
    """Seeded actor; there is no authentication, callers identify with X-User-Id."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(40), default="member")
