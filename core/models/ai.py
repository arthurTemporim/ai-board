from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, Text, func, select
from sqlalchemy.orm import Mapped, column_property, mapped_column

from core.db import Base
from core.models.card import Card
from core.models.enums import DraftKind, DraftStatus, RunStatus
from core.models.types import UTCDateTime, enum_column, utcnow


class AIRun(Base):
    """One workflow run or one AI step/call. Step runs point at their workflow run via parent_run_id."""

    __tablename__ = "ai_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_run.id", ondelete="CASCADE"), default=None, index=True
    )
    card_id: Mapped[int | None] = mapped_column(
        ForeignKey("card.id", ondelete="SET NULL"), default=None, index=True
    )
    kind: Mapped[str] = mapped_column(String(40))  # "workflow" or a step/node name
    status: Mapped[RunStatus] = mapped_column(enum_column(RunStatus), default=RunStatus.pending)
    prompt_name: Mapped[str | None] = mapped_column(String(80), default=None)
    prompt_version: Mapped[int | None] = mapped_column(default=None)
    model: Mapped[str | None] = mapped_column(String(80), default=None)
    input_hash: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    input: Mapped[dict | None] = mapped_column(JSON, default=None)
    output: Mapped[dict | None] = mapped_column(JSON, default=None)
    confidence: Mapped[float | None] = mapped_column(default=None)
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    tokens_in: Mapped[int | None] = mapped_column(default=None)
    tokens_out: Mapped[int | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)


class Draft(Base):
    """An AI-generated suggestion. Nothing changes on the card until a human applies it."""

    __tablename__ = "draft"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_run.id", ondelete="SET NULL"), default=None
    )
    kind: Mapped[DraftKind] = mapped_column(enum_column(DraftKind))
    payload: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(default=None)
    status: Mapped[DraftStatus] = mapped_column(enum_column(DraftStatus), default=DraftStatus.pending)
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), default=None
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Score(Base):
    """Rubric score for a card; the rubric is snapshotted so old scores stay interpretable."""

    __tablename__ = "score"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_run.id", ondelete="SET NULL"), default=None
    )
    rubric_name: Mapped[str] = mapped_column(String(80))
    rubric_version: Mapped[int] = mapped_column(default=1)
    per_criterion: Mapped[dict] = mapped_column(JSON)
    total: Mapped[float]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# Number of AI drafts waiting for a human decision; shown as an indicator on the card.
Card.pending_draft_count = column_property(
    select(func.count(Draft.id))
    .where(Draft.card_id == Card.id, Draft.status == DraftStatus.pending)
    .correlate_except(Draft)
    .scalar_subquery()
)
