from __future__ import annotations

from logging.config import fileConfig

from alembic import context

import core.models  # noqa: F401  (registers every table on Base.metadata)
from core.db import Base, database_url, make_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url") or database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Tests and tools can pass an engine via config.attributes["engine"].
    engine = config.attributes.get("engine") or make_engine(
        config.get_main_option("sqlalchemy.url") or None
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite cannot ALTER most things in place
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
