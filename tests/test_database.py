from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

import pytest

from app import database


class FakeCursor(AbstractContextManager["FakeCursor"]):
    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[str, ...] | None]] = []

    def __exit__(self, *_: object) -> None:
        return None

    def execute(
        self,
        query: str,
        parameters: tuple[str, ...] | None = None,
    ) -> None:
        self.queries.append((query, parameters))

    def fetchone(self) -> tuple[str, str]:
        return ("dolphinscheduler", "arena_backend")


class FakeConnection(AbstractContextManager["FakeConnection"]):
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self.cursor_instance


def test_check_database_runs_query(monkeypatch):
    connection = FakeConnection()
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    def fake_connect(*args: Any, **kwargs: Any) -> FakeConnection:
        assert args == ("postgresql://arena:secret@postgresql/dolphinscheduler",)
        assert kwargs == {"connect_timeout": 5}
        return connection

    monkeypatch.setattr(database, "connect", fake_connect)

    assert database.check_database(
        "postgresql://arena:secret@postgresql/dolphinscheduler"
    ) == database.DatabaseInfo(
        database="dolphinscheduler",
        schema="arena_backend",
    )
    assert connection.cursor_instance.queries == [
        (
            "SELECT set_config('search_path', %s, false)",
            ("arena_backend,public",),
        ),
        ("SELECT current_database(), current_schema()", None),
    ]


def test_check_database_requires_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(database.DatabaseError, match="DATABASE_URL"):
        database.check_database()
