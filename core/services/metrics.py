"""Metrics computed from real data: the audit log (every human decision about an AI suggestion) and cards.

Nothing here is estimated except the manual baseline, which is an explicit, configurable assumption
returned alongside the numbers so a reader can see which is which.
"""

from __future__ import annotations

import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import AuditEntry, Card

DECISION_ACTIONS = ("suggestion_accepted", "suggestion_rejected", "score_override", "duplicate_merged")
# A merge is recorded next to the acceptance of its link, so it is not a separate decision.
FIRST_DECISION_ACTIONS = ("suggestion_accepted", "suggestion_rejected", "score_override")

DEFAULT_MANUAL_TRIAGE_MINUTES = 15.0


@dataclass(frozen=True)
class Baseline:
    """What triage is assumed to cost without the AI workflow. An assumption, not a measurement."""

    manual_triage_minutes: float = DEFAULT_MANUAL_TRIAGE_MINUTES

    @classmethod
    def from_env(cls) -> Baseline:
        raw = os.environ.get("METRICS_BASELINE_TRIAGE_MINUTES")
        try:
            return cls(float(raw)) if raw else cls()
        except ValueError:
            return cls()


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def compute_metrics(session: Session, baseline: Baseline | None = None) -> dict[str, Any]:
    baseline = baseline or Baseline.from_env()
    entries = list(
        session.scalars(select(AuditEntry).where(AuditEntry.action.in_(DECISION_ACTIONS)).order_by(AuditEntry.id))
    )
    demo_entries = sum(1 for e in entries if e.action in FIRST_DECISION_ACTIONS and (e.detail or {}).get("demo"))

    # 1. Time from card creation to the first human decision on its suggestions.
    created = {cid: at for cid, at in session.execute(select(Card.id, Card.created_at))}
    first_decision: dict[int, Any] = {}
    for entry in entries:
        if entry.action in FIRST_DECISION_ACTIONS and entry.card_id in created and entry.card_id not in first_decision:
            first_decision[entry.card_id] = entry.created_at
    minutes = sorted(max(0.0, (at - created[cid]).total_seconds() / 60) for cid, at in first_decision.items())
    time_metric: dict[str, Any] = {
        "cards": len(minutes),
        "mean_minutes": round(statistics.fmean(minutes), 1) if minutes else None,
        "median_minutes": round(statistics.median(minutes), 1) if minutes else None,
        "slowest_minutes": round(minutes[-1], 1) if minutes else None,
    }

    # 2. Acceptance of AI suggestions.
    accepted = [e for e in entries if e.action == "suggestion_accepted"]
    rejected = [e for e in entries if e.action == "suggestion_rejected"]
    overridden = [e for e in entries if e.action == "score_override"]  # an acceptance with changed ratings
    kept = len(accepted) + len(overridden)
    decided = kept + len(rejected)
    changed = len([e for e in accepted if (e.detail or {}).get("edited")]) + len(overridden)
    by_type: dict[str, Counter] = defaultdict(Counter)
    for e in accepted:
        by_type[(e.detail or {}).get("type", "?")]["accepted"] += 1
    for _ in overridden:
        by_type["score"]["accepted"] += 1
    for e in rejected:
        by_type[(e.detail or {}).get("type", "?")]["rejected"] += 1
    acceptance = {
        "accepted": kept,
        "rejected": len(rejected),
        "decided": decided,
        "rate": _ratio(kept, decided),
        "accepted_without_changes_rate": _ratio(kept - changed, decided),
        "by_type": {
            t: {
                "accepted": c["accepted"],
                "rejected": c["rejected"],
                "rate": _ratio(c["accepted"], c["accepted"] + c["rejected"]),
            }
            for t, c in sorted(by_type.items())
        },
    }

    # 3. Duplicates consolidated.
    merges = [e for e in entries if e.action == "duplicate_merged"]
    duplicates = {
        "merged": len(merges),
        "votes_moved": sum(int((e.detail or {}).get("votes_moved", 0)) for e in merges),
        "duplicates_confirmed_as_suggested": len([e for e in accepted if (e.detail or {}).get("type") == "duplicate"]),
        "duplicates_rejected": len([e for e in rejected if (e.detail or {}).get("type") == "duplicate"]),
    }

    # 4. Score override rate: of the scores a person kept, how many did they change?
    score_accepts = len([e for e in accepted if (e.detail or {}).get("type") == "score"])
    score_overrides = len(overridden)
    overrides = {
        "overridden": score_overrides,
        "kept": score_accepts + score_overrides,
        "rate": _ratio(score_overrides, score_accepts + score_overrides),
        "scores_rejected": len([e for e in rejected if (e.detail or {}).get("type") == "score"]),
    }

    return {
        "time_to_first_decision": time_metric,
        "acceptance": acceptance,
        "duplicates": duplicates,
        "score_overrides": overrides,
        "demo_entries": demo_entries,
        "total_decisions": decided,
        "definitions": {
            "time_to_first_decision": "Elapsed minutes from a card's creation to the first accept, reject or override of an AI suggestion on it. Includes waiting time.",
            "acceptance": "Accepted (including score overrides) divided by accepted plus rejected suggestions. Pending suggestions are not counted.",
            "duplicates": "Duplicate suggestions a person accepted, each of which moves the duplicate's votes to the primary card and keeps the cards linked.",
            "score_overrides": "Score suggestions a person accepted after changing at least one rating, divided by all score suggestions they accepted (changed or not).",
        },
        "baseline": {
            "is_assumption": True,
            "manual_triage_minutes": baseline.manual_triage_minutes,
            "description": (
                f"Assumed {baseline.manual_triage_minutes:g} minutes of focused work to triage one request by hand (read it, "
                "search for duplicates, score it, write it up). This is an assumption, not a measurement: set "
                "METRICS_BASELINE_TRIAGE_MINUTES to your own figure. The measured value above is elapsed time including waiting, "
                "so the two are not like for like."
            ),
            "comparable": False,
        },
    }
