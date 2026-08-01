"""Backend PostgreSQL connectivity."""

from __future__ import annotations

import os
from collections.abc import Generator
from dataclasses import dataclass
from functools import lru_cache

from psycopg import Error, connect
from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

BACKEND_SCHEMA = "arena_backend"


class Base(DeclarativeBase):
    """Base class for tables managed by Backend Alembic migrations."""

    metadata = MetaData(schema=BACKEND_SCHEMA)


class DatabaseError(RuntimeError):
    """The backend database is not configured or cannot be reached."""


@dataclass(frozen=True)
class DatabaseInfo:
    database: str
    schema: str


def sqlalchemy_database_url(database_url: str | None = None) -> URL:
    """Build the SQLAlchemy URL, adding external PostgreSQL credentials."""
    current_database_url = database_url or os.getenv("DATABASE_URL", "")
    if not current_database_url:
        raise DatabaseError("DATABASE_URL 不能为空")
    url = make_url(current_database_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.username is None and os.getenv("POSTGRES_USER"):
        url = url.set(username=os.environ["POSTGRES_USER"], password=os.getenv("POSTGRES_PASSWORD"))
    return url


@lru_cache
def database_engine() -> Engine:
    """Return the shared SQLAlchemy engine."""
    return create_engine(sqlalchemy_database_url(), pool_pre_ping=True)


@lru_cache
def database_session_factory() -> sessionmaker[Session]:
    """Return the shared SQLAlchemy session factory."""
    return sessionmaker(bind=database_engine(), autoflush=False, expire_on_commit=False)


def get_database_session() -> Generator[Session, None, None]:
    """Provide one request-scoped database session."""
    with database_session_factory()() as session:
        yield session


def check_database(database_url: str | None = None) -> DatabaseInfo:
    """Verify the backend database and migrated schema are available."""
    current_database_url = database_url or os.getenv("DATABASE_URL", "")
    if not current_database_url:
        raise DatabaseError("DATABASE_URL 不能为空")

    connection_options: dict[str, str | int] = {"connect_timeout": 5}
    if os.getenv("POSTGRES_USER"):
        connection_options["user"] = os.environ["POSTGRES_USER"]
    if os.getenv("POSTGRES_PASSWORD"):
        connection_options["password"] = os.environ["POSTGRES_PASSWORD"]

    try:
        with (
            connect(current_database_url, **connection_options) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT set_config('search_path', %s, false)",
                (f"{BACKEND_SCHEMA},public",),
            )
            cursor.execute("SELECT current_database(), current_schema()")
            row = cursor.fetchone()
    except Error as error:
        raise DatabaseError(f"PostgreSQL 连接失败: {error}") from error

    if row is None:
        raise DatabaseError("PostgreSQL 未返回当前数据库和 schema")
    database_name, schema_name = map(str, row)
    if schema_name != BACKEND_SCHEMA:
        raise DatabaseError(
            f"Backend schema 未迁移: 期望 {BACKEND_SCHEMA}，实际 {schema_name}"
        )
    return DatabaseInfo(database=database_name, schema=schema_name)
