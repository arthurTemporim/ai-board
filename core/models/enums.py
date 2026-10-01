from __future__ import annotations

from enum import StrEnum


class RequesterType(StrEnum):
    customer = "customer"
    internal = "internal"
    partner = "partner"
    unknown = "unknown"


class LinkKind(StrEnum):
    related = "related"
    duplicate = "duplicate"


class LinkStatus(StrEnum):
    suggested = "suggested"
    confirmed = "confirmed"
    rejected = "rejected"  # a person said no; AI re-runs must not propose it again


class DraftKind(StrEnum):
    tag = "tag"
    comment = "comment"
    analysis = "analysis"
    link = "link"
    problem = "problem"
    score = "score"
    brief = "brief"
    comms = "comms"
    field_edit = "field_edit"


class DraftStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    applied = "applied"


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    awaiting_human = "awaiting_human"
    skipped = "skipped"
    succeeded = "succeeded"
    failed = "failed"
