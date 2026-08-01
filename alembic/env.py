"""Alembic runtime configuration."""

from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context
import apps.backtest.models
import apps.factor.models
import apps.query.models
import apps.users.models
from config.database import BACKEND_SCHEMA, Base, sqlalchemy_database_url

VERSION_TABLE = "alembic_version_backend"
ARENA_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ARENA_ROOT / ".env", override=False)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_name(
    name: str | None,
    type_: str,
    parent_names: dict[str, str | None],
) -> bool:
    """Restrict autogenerate reflection to Backend-owned objects."""
    if type_ == "schema":
        return name == BACKEND_SCHEMA
    return parent_names.get("schema_name") == BACKEND_SCHEMA


def sqlalchemy_url() -> str:
    return sqlalchemy_database_url().render_as_string(hide_password=False).replace("%", "%%")


def run_migrations_offline() -> None:
    context.configure(
        url=sqlalchemy_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
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
            include_schemas=True,
            include_name=include_name,
            version_table=VERSION_TABLE,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
