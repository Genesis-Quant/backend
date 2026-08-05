from __future__ import annotations

from datetime import UTC, datetime

import pytest
from runtime.config import ArenaSettings as RuntimeArenaSettings
from runtime.manage.apps import build_parser
from runtime.utils.storage import (
    ObjectInfo,
    ObjectStorage,
    ObjectStorageConfigurationError,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.apps.incremental.models import IncrementalWorkflowRun
from core.apps.users.models import User
from core.apps.workflows.models import WorkflowInstance, WorkflowRun
from core.database.base import Base
from core.utils import results


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            WorkflowRun.__table__,
            IncrementalWorkflowRun.__table__,
            WorkflowInstance.__table__,
        ],
    )
    with Session(engine) as active_session:
        yield active_session


def successful_run(
    session: Session,
    output_dir: str,
) -> tuple[User, IncrementalWorkflowRun]:
    user = User(username="owner", password_hash="test")
    session.add(user)
    session.flush()
    run = IncrementalWorkflowRun(
        user_id=user.id,
        application="incremental",
        submission_state="SUBMITTED",
        payload={},
        requested_outputs=["data"],
        output_dir=output_dir,
        events=[],
    )
    session.add(run)
    session.flush()
    session.add(
        WorkflowInstance(
            workflow_instance_id=101,
            workflow_run_id=run.id,
            state="SUCCESS",
            is_current=True,
            state_history=[],
        )
    )
    session.commit()
    return user, run


def test_cloud_result_listing_and_download_redirect(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = successful_run(
        session,
        "s3://arena-bucket/arena-runtime/query/workspace/output",
    )
    instances: list[FakeStorage] = []

    def create_storage() -> FakeStorage:
        storage = FakeStorage()
        instances.append(storage)
        return storage

    monkeypatch.setattr(results.ObjectStorage, "from_env", staticmethod(create_storage))

    files = results.result_files(
        session,
        user.id,
        101,
        "incremental",
        {"data": "query.parquet"},
    )
    response = results.result_response(
        session,
        user.id,
        101,
        "data",
        "incremental",
        {"data": "query.parquet"},
    )

    assert files == [{
        "name": "data",
        "filename": "query.parquet",
        "size": 7,
        "modified_at": datetime(2026, 8, 4, tzinfo=UTC),
    }]
    assert response.status_code == 307
    assert response.headers["location"] == "https://storage.example/query.parquet"
    assert all(storage.closed for storage in instances)


def test_local_result_listing_and_download(
    session: Session,
    tmp_path,
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output = output_dir / "query.parquet"
    output.write_bytes(b"local")
    user, _ = successful_run(session, str(output_dir))

    files = results.result_files(
        session,
        user.id,
        101,
        "incremental",
        {"data": "query.parquet"},
    )
    response = results.result_response(
        session,
        user.id,
        101,
        "data",
        "incremental",
        {"data": "query.parquet"},
    )

    assert files[0]["size"] == 5
    assert str(response.path) == str(output)


def test_cloud_result_directory_is_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = FakeStorage()
    monkeypatch.setattr(
        results.ObjectStorage,
        "from_env",
        staticmethod(lambda: storage),
    )

    results.delete_result_objects(
        "s3://arena-bucket/arena-runtime/query/workspace/output"
    )

    assert storage.deleted_key == "arena-runtime/query/workspace/output"
    assert storage.closed is True


def test_object_storage_uri_must_use_configured_bucket_and_root() -> None:
    storage = ObjectStorage(object(), "arena-bucket", "arena-runtime")

    assert storage.key_from_uri(
        "s3://arena-bucket/arena-runtime/query/workspace/output"
    ) == "arena-runtime/query/workspace/output"
    with pytest.raises(ObjectStorageConfigurationError):
        storage.key_from_uri("s3://other-bucket/arena-runtime/query/output")
    with pytest.raises(ObjectStorageConfigurationError):
        storage.key_from_uri("s3://arena-bucket/outside-root/query/output")
    with pytest.raises(ObjectStorageConfigurationError):
        storage.delete_prefix("arena-runtime")


def test_object_storage_deletes_each_object_for_s3_compatibility() -> None:
    client = FakeDeleteClient()
    storage = ObjectStorage(client, "arena-bucket", "arena-runtime")

    storage.delete_prefix("arena-runtime/query/workspace/output")

    assert client.deleted == [
        ("arena-bucket", "arena-runtime/query/workspace/output/first.parquet"),
        ("arena-bucket", "arena-runtime/query/workspace/output/second.parquet"),
    ]


def test_runtime_cloud_defaults_to_environment_and_can_be_overridden(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_file = tmp_path / "query.json"
    input_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(RuntimeArenaSettings, "SHARED_CLOUD", True)
    parser = build_parser()

    inherited = parser.parse_args([
        "query",
        "--input-file",
        str(input_file),
        "--output-dir",
        "query/output",
        "--output",
        "data",
    ])
    overridden = parser.parse_args([
        "query",
        "--input-file",
        str(input_file),
        "--output-dir",
        str(tmp_path / "output"),
        "--output",
        "data",
        "--cloud",
        "false",
    ])

    assert inherited.cloud is True
    assert inherited.output_dir == "query/output"
    assert overridden.cloud is False


class FakeStorage:
    def __init__(self) -> None:
        self.closed = False
        self.deleted_key: str | None = None

    def key_from_uri(self, uri: str) -> str:
        assert uri == "s3://arena-bucket/arena-runtime/query/workspace/output"
        return "arena-runtime/query/workspace/output"

    def object_info(self, key: str) -> ObjectInfo:
        assert key == "arena-runtime/query/workspace/output/query.parquet"
        return ObjectInfo(7, datetime(2026, 8, 4, tzinfo=UTC))

    def download_url(self, key: str) -> str:
        assert key == "arena-runtime/query/workspace/output/query.parquet"
        return "https://storage.example/query.parquet"

    def delete_prefix(self, key: str) -> None:
        self.deleted_key = key

    def close(self) -> None:
        self.closed = True


class FakeDeleteClient:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        return FakePaginator()

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.deleted.append((Bucket, Key))


class FakePaginator:
    def paginate(self, *, Bucket: str, Prefix: str):
        assert Bucket == "arena-bucket"
        assert Prefix == "arena-runtime/query/workspace/output/"
        return [{
            "Contents": [
                {"Key": f"{Prefix}first.parquet"},
                {"Key": f"{Prefix}second.parquet"},
            ]
        }]
