"""Demo data for the metrics page: python -m data.seed.demo [--force]

Adds a few new requests to the seeded boards, runs the real triage workflow on them in mock mode (no
model, no key), then makes human-like decisions on the suggestions through the same service the API
uses. Decisions are backdated to a few minutes after each card's creation, and every audit entry is
flagged `demo`, so the metrics page can say plainly that this is sample data.

Run after `make seed`. It adds to the database; `make seed` wipes it again.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import timedelta

from sqlalchemy import select

import core.models  # noqa: F401
from core.db import make_session_factory, session_scope
from core.models import AuditEntry, Card, Draft, DraftStatus, User, Vote
from core.models.types import utcnow
from core.services.cards import CardService
from core.services.decisions import DecisionService

ACTOR = 1  # Alice Martin, a product manager in the seed

# (title, description, votes by user id, minutes after creation of the first decision, plan)
# plan: what the "person" does with each kind of suggestion.
DEMO_CARDS = [
    dict(
        title="Support SAML single sign-on please",
        description="Our IT team requires SAML 2.0 SSO before we can roll the product out beyond the pilot group.",
        voters=[1],
        first_decision_min=6,
        plan={"duplicate": "accept", "related": "accept"},
    ),
    dict(
        title="Dark mode option",
        description="Please add a dark color theme.",
        voters=[2],
        first_decision_min=11,
        plan={"duplicate": "reject:Different scope: this one is about high-contrast themes", "related": "reject"},
    ),
    dict(
        title="Offline mode for field technicians",
        description="Technicians work in basements and rural sites without signal and need to read and update records offline.",
        voters=[3, 4],
        first_decision_min=9,
        plan={"score": "accept", "tag": "accept", "brief": "accept", "comms": "accept", "analysis": "accept", "related": "reject"},
    ),
    dict(
        title="Bulk import of customers from a spreadsheet",
        description="Our onboarding team receives customer lists as spreadsheets and re-types every row. We want to upload them and map the columns.",
        voters=[5],
        first_decision_min=14,
        plan={
            "score": "override:Strategic fit is lower: onboarding is handled by services, not self-serve",
            "tag": "accept",
            "brief": "accept",
            "comms": "reject",
            "new_problem": "accept",
            "related": "accept",
        },
    ),
    dict(
        title="Make it faster",
        description="Slow.",
        voters=[],
        first_decision_min=4,
        plan={"comment": "accept", "comms": "accept", "analysis": "accept"},
    ),
]


def already_seeded() -> bool:
    with session_scope(make_session_factory()) as session:
        return any((e.detail or {}).get("demo") for e in session.scalars(select(AuditEntry)))


def create_cards(session) -> list[int]:
    """New cards in the first board's Inbox, backdated so their ages look like a working morning."""
    service = CardService(session)
    board_id = service.boards.list()[0].id
    ids = []
    now = utcnow()
    for index, spec in enumerate(DEMO_CARDS):
        card = service.create_card(
            board_id=board_id,
            title=spec["title"],
            description=spec["description"],
            requester_type="customer",
            created_by_id=ACTOR,
        )
        card.created_at = now - timedelta(hours=3, minutes=index * 7)
        for user_id in spec["voters"]:
            if session.get(User, user_id) is not None:
                session.add(Vote(card_id=card.id, user_id=user_id))
        ids.append(card.id)
    session.flush()
    return ids


def decide(session, card_id: int, plan: dict[str, str]) -> int:
    """Apply the plan to every pending suggestion on the card; returns how many decisions were made."""
    service = DecisionService(session)
    made = 0
    for suggestion in service.list(card_id):
        action = plan.get(suggestion.type)
        if action is None:
            continue
        verb, _, argument = action.partition(":")
        try:
            if verb == "accept":
                service.accept(card_id, suggestion.id, ACTOR)
            elif verb == "override":
                line = next(line for line in suggestion.data["lines"] if line["key"] == "strategic_fit")
                lowered = line["rating"] - 1 if line["rating"] > 1 else line["rating"] + 1  # always a real change
                service.accept(
                    card_id, suggestion.id, ACTOR, override={"ratings": {"strategic_fit": lowered}, "reason": argument}
                )
            elif verb == "reject":
                service.reject(card_id, suggestion.id, ACTOR, reason=argument or None)
            else:
                continue
        except Exception as exc:  # a demo should not stop because one suggestion was already stale
            print(f"  skipped {suggestion.id}: {exc}")
            continue
        made += 1
    return made


def backdate(session, card_id: int, first_decision_min: int) -> None:
    """Move this card's audit entries and draft decisions to just after its creation, and flag them demo."""
    card = session.get(Card, card_id)
    entries = list(session.scalars(select(AuditEntry).where(AuditEntry.card_id == card_id).order_by(AuditEntry.id)))
    for offset, entry in enumerate(entries):
        entry.created_at = card.created_at + timedelta(minutes=first_decision_min + offset * 0.5)
        entry.detail = {**(entry.detail or {}), "demo": True}
    for draft in session.scalars(select(Draft).where(Draft.card_id == card_id, Draft.status != DraftStatus.pending)):
        draft.decided_at = card.created_at + timedelta(minutes=first_decision_min)


async def run_triage(card_ids: list[int]) -> None:
    from ai.graph.state import GraphConfig
    from ai.graph.workflow import Workflow

    async with Workflow(config=GraphConfig.from_env()) as workflow:
        for card_id in card_ids:
            report = await workflow.start(card_id, created_by_id=ACTOR)
            print(f"  card {card_id}: run {report.run_id} {report.status} ({report.outcome})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="add demo data even if some already exists")
    args = parser.parse_args()

    os.environ["MOCK_AI"] = "1"  # deterministic and free; a demo must not depend on a model being up
    if already_seeded() and not args.force:
        print("Demo data is already there (use --force to add more, or `make seed` to start over).")
        return

    factory = make_session_factory()
    with session_scope(factory) as session:
        card_ids = create_cards(session)
    print(f"Created {len(card_ids)} demo cards; running AI triage (mock mode)...")
    asyncio.run(run_triage(card_ids))

    total = 0
    with session_scope(factory) as session:
        for card_id, spec in zip(card_ids, DEMO_CARDS, strict=True):
            made = decide(session, card_id, spec["plan"])
            backdate(session, card_id, spec["first_decision_min"])
            total += made
            print(f"  card {card_id} {spec['title']!r}: {made} decision(s)")
    print(f"Recorded {total} demo decision(s). Open the Metrics page.")


if __name__ == "__main__":
    main()
