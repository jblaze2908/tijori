"""Alembic environment. Migrations run as the owner role, never as the runtime app role."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from tijori.models import Base

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    # Tests pass the URL via attributes so a password never lands in alembic.ini.
    url = config.attributes.get("url") or os.environ.get("TIJORI_ADMIN_DATABASE_URL") or os.environ.get(
        "TIJORI_DATABASE_URL"
    )
    if not url:
        raise RuntimeError("set TIJORI_ADMIN_DATABASE_URL (owner role) to run migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
