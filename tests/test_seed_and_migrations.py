from __future__ import annotations

import json
from collections import Counter

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select

import core.models  # noqa: F401
from core.db import Base, make_engine
from core.models import Card, Column, Comment, Feedback, Tag, Vote
from data.seed import seed as seed_module
from data.seed import seed_data


def test_seed_loads_expected_shape(session_factory):
    with session_factory() as session:
        counts = seed_module.seed(session)
        session.commit()

        assert counts == {"users": 8, "boards": 2, "columns": 14, "tags": 14, "cards": 30}
        assert session.scalar(select(func.count(Column.id))) == 14
        assert session.scalar(select(func.count(Vote.id))) > 60
        assert session.scalar(select(func.count(Comment.id))) >= 20
        assert session.scalar(select(func.count(Feedback.id))) >= 15
        assert all(t.color.startswith("#") and len(t.color) == 7 for t in session.scalars(select(Tag)))

        cards = session.scalars(select(Card)).all()
        assert len({c.requester_type for c in cards}) == 4
        assert len({c.column_id for c in cards}) >= 10  # spread across columns
        for column_id, n in Counter(c.column_id for c in cards).items():
            positions = sorted(c.position for c in cards if c.column_id == column_id)
            assert positions == list(range(n))


def test_seed_refuses_to_run_twice_without_reset(session_factory):
    with session_factory() as session:
        seed_module.seed(session)
        session.commit()
        try:
            seed_module.seed(session)
        except RuntimeError as exc:
            assert "--reset" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
        seed_module.reset(session)
        assert seed_module.seed(session)["cards"] == 30


def test_labels_json_is_in_sync_and_points_at_real_cards(session_factory):
    committed = seed_module.LABELS_PATH.read_text(encoding="utf-8")
    assert committed == seed_module.labels_json(), "run: python -m data.seed.seed --write-labels"

    labels = json.loads(committed)
    with session_factory() as session:
        seed_module.seed(session)
        titles = {c.id: c.title for c in session.scalars(select(Card))}
    assert {int(i): v["title"] for i, v in labels["cards"].items()} == titles

    assert 4 <= len(labels["duplicate_groups"]) <= 5
    assert len(labels["related_pairs"]) == 3
    assert len(labels["vague_card_ids"]) >= 2
    duplicated = {i for g in labels["duplicate_groups"] for i in g["card_ids"]}
    related = {i for pair in labels["related_pairs"] for i in pair}
    assert all(i in titles for i in duplicated | related)
    # Related-but-distinct pairs must never be labelled as duplicates of each other.
    dup_pairs = {tuple(p) for p in labels["duplicate_pairs"]}
    assert not any(tuple(p) in dup_pairs for p in labels["related_pairs"])
    assert len(seed_data.CARDS) == 30


def test_migration_creates_exactly_the_model_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    engine = make_engine(url)
    config = Config("alembic.ini")
    config.attributes["engine"] = engine

    command.upgrade(config, "head")

    assert set(inspect(engine).get_table_names()) >= set(Base.metadata.tables) | {"alembic_version"}
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []

    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
