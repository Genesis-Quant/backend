from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from config import ArenaSettings
from core.apps.admin import services
from core.apps.admin.services import AdminService
from core.apps.query.models import QueryProject
from core.apps.users.models import User
from core.apps.workflows.models import WorkflowWorkspace
from core.database.base import Base

OWNED_KEY = "3a809554ba8f4c75a5cf46ec441994af"
ORPHAN_KEY = "0f6f75f251d24e1e9d0099c933a591fe"


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as active_session:
        yield active_session


def write_bytes(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def owned_query_run(session: Session) -> tuple[WorkflowWorkspace, QueryProject]:
    user = User(username="owner", password_hash="test")
    session.add(user)
    session.flush()
    run = WorkflowWorkspace(
        user_id=user.id,
        application="query",
        workspace_key=OWNED_KEY,
    )
    session.add(run)
    session.flush()
    project = QueryProject(user_id=user.id, workflow_workspace_id=run.id, title="价格查询")
    session.add(project)
    session.commit()
    return run, project


def test_local_output_storage_groups_by_workspace_and_resolves_owner(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, project = owned_query_run(session)
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    write_bytes(tmp_path / "query" / OWNED_KEY / "input.json", 100)
    write_bytes(tmp_path / "query" / OWNED_KEY / "output" / "query.parquet", 20)
    write_bytes(tmp_path / "factor" / ORPHAN_KEY / "output" / "factor.parquet", 80)
    write_bytes(tmp_path / "factor" / ORPHAN_KEY / "output" / "nested" / "summary.json", 5)

    result = AdminService.output_storage(session)

    assert result["available"] is True
    assert result["mode"] == "local"
    assert result["root"] == str(tmp_path.resolve())
    assert result["workspace_count"] == 2
    assert result["orphan_workspace_count"] == 1
    assert result["file_count"] == 3
    assert result["total_bytes"] == 105
    assert result["applications"] == [
        {
            "application": "factor",
            "workspace_count": 1,
            "file_count": 2,
            "total_bytes": 85,
        },
        {
            "application": "query",
            "workspace_count": 1,
            "file_count": 1,
            "total_bytes": 20,
        },
    ]
    workspaces = {
        item["workspace_key"]: item
        for item in result["workspaces"]
    }
    assert workspaces[ORPHAN_KEY]["orphaned"] is True
    owned = workspaces[OWNED_KEY]
    assert isinstance(owned["modified_at"], datetime)
    assert {key: value for key, value in owned.items() if key != "modified_at"} == {
        "application": "query",
        "workspace_key": OWNED_KEY,
        "path": f"query/{OWNED_KEY}/output",
        "storage": "local",
        "file_count": 1,
        "size_bytes": 20,
        "orphaned": False,
        "workflow_workspace_id": run.id,
        "project_id": project.id,
        "project_title": "价格查询",
    }


def test_missing_local_output_storage_reports_unavailable(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", missing)

    result = AdminService.output_storage(session)

    assert result == {
        "available": False,
        "error": f"共享输出目录不存在: {missing.resolve()}",
        "mode": "local",
        "root": str(missing.resolve()),
        "workspace_count": 0,
        "orphan_workspace_count": 0,
        "file_count": 0,
        "total_bytes": 0,
        "applications": [],
        "workspaces": [],
    }


def test_cloud_output_storage_groups_objects_by_workspace(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FakeStorage()
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    monkeypatch.setattr(services.ObjectStorage, "from_env", lambda: storage)

    result = AdminService.output_storage(session)

    assert result["available"] is True
    assert result["mode"] == "cloud"
    assert result["root"] == "s3://arena/arena-runtime"
    assert result["workspace_count"] == 1
    assert result["orphan_workspace_count"] == 1
    assert result["file_count"] == 1
    assert result["total_bytes"] == 42
    assert result["workspaces"] == [{
        "application": "query",
        "workspace_key": ORPHAN_KEY,
        "path": f"query/{ORPHAN_KEY}/output",
        "storage": "cloud",
        "file_count": 1,
        "size_bytes": 42,
        "modified_at": datetime(2026, 8, 6, tzinfo=UTC),
        "orphaned": True,
        "workflow_workspace_id": None,
        "project_id": None,
        "project_title": None,
    }]
    assert storage.closed is True


def test_delete_orphan_workspace_removes_whole_local_domain(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    workspace = tmp_path / "query" / ORPHAN_KEY
    write_bytes(workspace / "input.json", 10)
    write_bytes(workspace / "output" / "query.parquet", 20)

    result = AdminService.delete_orphan_workspace(session, "query", ORPHAN_KEY)

    assert result == {"application": "query", "workspace_key": ORPHAN_KEY}
    assert not workspace.exists()


def test_delete_owned_workspace_is_rejected(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owned_query_run(session)
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="仍归属于工作流工作空间"):
        AdminService.delete_orphan_workspace(session, "query", OWNED_KEY)


def test_delete_orphan_workspace_removes_cloud_and_local_domain(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FakeStorage()
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    monkeypatch.setattr(services.ObjectStorage, "from_env", lambda: storage)
    workspace = tmp_path / "incremental" / ORPHAN_KEY
    write_bytes(workspace / "output" / "message.json", 10)

    AdminService.delete_orphan_workspace(session, "incremental", ORPHAN_KEY)

    assert storage.deleted_key == f"arena-runtime/incremental/{ORPHAN_KEY}/output"
    assert storage.closed is True
    assert not workspace.exists()


class FakePaginator:
    def paginate(self, **_: object) -> list[dict[str, object]]:
        return [{
            "Contents": [
                {
                    "Key": f"arena-runtime/query/{ORPHAN_KEY}/output/query.parquet",
                    "Size": 42,
                    "LastModified": datetime(2026, 8, 6, tzinfo=UTC),
                },
                {
                    "Key": f"arena-runtime/query/{ORPHAN_KEY}/input.json",
                    "Size": 100,
                    "LastModified": datetime(2026, 8, 6, tzinfo=UTC),
                },
            ]
        }]


class FakeClient:
    @staticmethod
    def get_paginator(_: str) -> FakePaginator:
        return FakePaginator()


class FakeStorage:
    bucket = "arena"
    root_folder = "arena-runtime"
    client = FakeClient()

    def __init__(self) -> None:
        self.closed = False
        self.deleted_key: str | None = None

    def object_key(self, key: str) -> str:
        return f"{self.root_folder}/{key}"

    def delete_prefix(self, key: str) -> None:
        self.deleted_key = key

    def close(self) -> None:
        self.closed = True
