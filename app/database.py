"""Backend PostgreSQL connectivity."""

from __future__ import annotations

import os
from dataclasses import dataclass

from psycopg import Error, connect

BACKEND_SCHEMA = "arena_backend"


class DatabaseError(RuntimeError):
    """The backend database is not configured or cannot be reached."""


@dataclass(frozen=True)
class DatabaseInfo:
    database: str
    schema: str


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
