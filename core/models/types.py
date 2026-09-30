from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC (SQLite drops tzinfo) and always returns timezone-aware UTC values."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        return None if value is None else value.replace(tzinfo=UTC)


def enum_column(enum_cls: type[PyEnum]) -> Enum:
    """Stores the enum *value* as a short string (no native DB enum type)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=20,
        validate_strings=True,
        values_callable=lambda e: [member.value for member in e],
    )
