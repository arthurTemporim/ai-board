"""Load demo data. Usage: python -m data.seed.seed [--reset] [--write-labels]"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import core.models  # noqa: F401
from core.db import Base, session_scope
from core.models import Board, Card, Column, Comment, Feedback, RequesterType, Tag, User, Vote
from core.models.types import utcnow
from core.services.boards import DEFAULT_COLUMNS
from data.seed import seed_data as data

LABELS_PATH = Path(__file__).with_name("labels.json")


def labels_json() -> str:
    return json.dumps(data.build_labels(), indent=2, ensure_ascii=False) + "\n"


def reset(session: Session) -> None:
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(delete(table))


def seed(session: Session) -> dict[str, int]:
    """Insert everything into an empty database; returns counts."""
    if session.scalar(select(Board.id).limit(1)) is not None:
        raise RuntimeError("Database already has data; run with --reset to replace it")

    now = utcnow()
    users = [User(name=name, role=role) for name, role in data.USERS]
    session.add_all(users)

    boards, columns, tags = [], {}, {}
    for index, spec in enumerate(data.BOARDS):
        board = Board(name=spec["name"], description=spec["description"], created_at=now - timedelta(days=90))
        session.add(board)
        session.flush()
        boards.append(board)
        for position, name in enumerate(DEFAULT_COLUMNS):
            column = Column(board_id=board.id, name=name, position=position)
            session.add(column)
            session.flush()
            columns[(index, name)] = column
        for name, color in data.TAGS[index]:
            tag = Tag(board_id=board.id, name=name, color=color)
            session.add(tag)
            session.flush()
            tags[(index, name)] = tag

    positions: dict[int, int] = {}
    for number, spec in enumerate(data.CARDS, start=1):
        board_index = spec["board"]
        column = columns[(board_index, spec["column"])]
        created = now - timedelta(days=spec["age"])
        card = Card(
            id=number,
            board_id=boards[board_index].id,
            column_id=column.id,
            position=positions.get(column.id, 0),
            title=spec["title"],
            description=spec["description"],
            requester_type=RequesterType(spec["requester"]),
            progress=spec["progress"],
            created_by_id=users[number % len(users)].id,
            created_at=created,
            updated_at=created,
            tags=[tags[(board_index, name)] for name in spec["tags"]],
        )
        positions[column.id] = card.position + 1
        session.add(card)
        session.flush()

        for k in range(spec["votes"]):
            session.add(
                Vote(
                    card_id=card.id,
                    user_id=users[(number + k) % len(users)].id,
                    created_at=min(now, created + timedelta(hours=6 * (k + 1))),
                )
            )
        for k, (author, body) in enumerate(spec["comments"]):
            session.add(
                Comment(
                    card_id=card.id,
                    author_id=users[author].id,
                    body=body,
                    created_at=min(now, created + timedelta(days=k + 1)),
                )
            )
        for k, (source, quote, ref) in enumerate(spec["feedback"]):
            session.add(
                Feedback(
                    card_id=card.id,
                    source=source,
                    quote=quote,
                    customer_ref=ref,
                    created_at=min(now, created + timedelta(days=k)),
                )
            )
    session.flush()
    return {
        "users": len(users),
        "boards": len(boards),
        "columns": len(columns),
        "tags": len(tags),
        "cards": len(data.CARDS),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete all existing rows first")
    parser.add_argument("--write-labels", action="store_true", help="regenerate labels.json and exit")
    args = parser.parse_args()

    if args.write_labels:
        LABELS_PATH.write_text(labels_json(), encoding="utf-8")
        print(f"wrote {LABELS_PATH}")
        return

    with session_scope() as session:
        if args.reset:
            reset(session)
        counts = seed(session)
    print("seeded " + ", ".join(f"{n} {k}" for k, n in counts.items()))


if __name__ == "__main__":
    main()
