"""Alembic migration environment; never embeds credentials in source."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

import platform_app.models  # noqa: F401 - register all mapped tables
from platform_app.model_base import Base


def database_url() -> str:
    url = os.environ.get("AIP_DATABASE_URL")
    if not url or not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("AIP_DATABASE_URL must be an explicit PostgreSQL URL for migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(), target_metadata=Base.metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
