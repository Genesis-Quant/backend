"""Alembic runtime configuration."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

import core.apps.admin.models  # noqa: F401 -- register tables in Base.metadata
import core.apps.backtest.models  # noqa: F401 -- register tables in Base.metadata
import core.apps.factor.models  # noqa: F401 -- register tables in Base.metadata
import core.apps.feedback.models  # noqa: F401 -- register tables in Base.metadata
import core.apps.query.models  # noqa: F401 -- register tables in Base.metadata
import core.apps.users.models  # noqa: F401 -- register tables in Base.metadata
import core.apps.workflows.models  # noqa: F401 -- register tables in Base.metadata
from alembic import context
from core.database.base import Base
from core.database.session import sqlalchemy_database_url

VERSION_TABLE = "alembic_version_backend"
ARENA_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ARENA_ROOT / ".env", override=False)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def sqlalchemy_url() -> str:
    return sqlalchemy_database_url().render_as_string(hide_password=False).replace("%", "%%")


def run_migrations_offline() -> None:
    context.configure(
        url=sqlalchemy_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = sqlalchemy_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
