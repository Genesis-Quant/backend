from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException
from runtime.config import ArenaSettings as RuntimeArenaSettings
from runtime.manage.apps import build_parser
from runtime.utils.storage import (
    ObjectInfo,
    ObjectStorage,
    ObjectStorageConfigurationError,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from config import ArenaSettings
from core.apps.backtest.services import backtest_result_files
from core.apps.users.models import User
from core.apps.workflows.artifacts import workspace_output_directory
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace
from core.mcp.schemas import WorkflowOutputFile
from core.utils.results import OUTPUTS_VALIDATED_EVENT, ResultFile
from core.database.base import Base
from core.utils import results
from core.utils.http import raise_api_http_error

WORKSPACE_KEY = "3a809554ba8f4c75a5cf46ec441994af"


def parquet_bytes() -> bytes:
    output = BytesIO()
    pd.DataFrame({
        "time": pd.to_datetime(["2026-08-01", "2026-08-02"]),
        "code": ["000001.XSHE", "600000.XSHG"],
        "value": [1.5, 2.5],
    }).to_parquet(output, index=False)
    return output.getvalue()


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            WorkflowWorkspace.__table__,
            WorkflowAttempt.__table__,
            WorkflowInstance.__table__,
        ],
    )
    with Session(engine) as active_session:
        yield active_session


@pytest.fixture(autouse=True)
def reset_result_metadata_cache() -> None:
    results.clear_result_metadata_cache()


def successful_run(
    session: Session,
    *,
    application: str = "query",
    requested_outputs: list[str] | None = None,
) -> tuple[User, WorkflowWorkspace]:
    user = User(username="owner", password_hash="test")
    session.add(user)
    session.flush()
    run = WorkflowWorkspace(
        user_id=user.id,
        application=application,
        workspace_key=WORKSPACE_KEY,
    )
    session.add(run)
    session.flush()
    attempt = WorkflowAttempt(
        workflow_workspace_id=run.id,
        is_current=True,
        submission_state="WORKFLOW_CREATED",
        input_json={},
        start_parameters={},
        requested_outputs=["data"] if requested_outputs is None else requested_outputs,
        events=[{
            "event": OUTPUTS_VALIDATED_EVENT,
            "workflow_instance_id": 101,
        }],
    )
    session.add(attempt)
    session.flush()
    session.add(
        WorkflowInstance(
            workflow_instance_id=101,
            workflow_attempt_id=attempt.id,
            state="SUCCESS",
            state_history=[],
        )
    )
    session.commit()
    return user, run


def test_cloud_result_listing_and_download_redirect(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = successful_run(session)
    instances: list[FakeStorage] = []
    data = parquet_bytes()

    def create_storage() -> FakeStorage:
        storage = FakeStorage(data)
        instances.append(storage)
        return storage

    monkeypatch.setattr(results.ObjectStorage, "from_env", staticmethod(create_storage))
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)

    files = results.result_files(
        session,
        user.id,
        101,
        "query",
        {"data": "query.parquet"},
    )
    # The cache is tied to the exact cloud size/mtime/ETag snapshot, so the
    # second listing performs HEAD but does not download the object again.
    cached_files = results.result_files(
        session,
        user.id,
        101,
        "query",
        {"data": "query.parquet"},
    )
    response = results.result_response(
        session,
        user.id,
        101,
        "data",
        "query",
        {"data": "query.parquet"},
    )

    assert files == [{
        "name": "data",
        "filename": "query.parquet",
        "size": len(data),
        "modified_at": datetime(2026, 8, 4, tzinfo=UTC),
        "row_count": 2,
        "columns": [
            {"name": "time", "type": "timestamp[ns]", "nullable": True},
            {"name": "code", "type": "string", "nullable": True},
            {"name": "value", "type": "double", "nullable": True},
        ],
        "sha256": hashlib.sha256(data).hexdigest(),
    }]
    assert cached_files == files
    assert response.status_code == 307
    assert response.headers["location"] == "https://storage.example/query.parquet"
    assert [storage.client.get_count for storage in instances] == [1, 0, 0]
    assert all(storage.closed for storage in instances)

    http_output = ResultFile[str].model_validate(files[0]).model_dump(mode="json")
    mcp_output = WorkflowOutputFile(
        **files[0],
        download_path="/api/v1/query/workflows/101/outputs/data",
    ).model_dump(mode="json")
    for output in (http_output, mcp_output):
        assert output["row_count"] == 2
        assert output["columns"][0] == {
            "name": "time",
            "type": "timestamp[ns]",
            "nullable": True,
        }
        assert output["sha256"] == hashlib.sha256(data).hexdigest()


def test_cloud_result_listing_uses_runtime_manifest_without_downloading_parquet(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = successful_run(session)
    data = parquet_bytes()
    metadata = results.parquet_result_metadata(BytesIO(data), 0, "data")
    manifest = results.ResultManifest(files={
        "data": results.ResultManifestEntry(
            filename="query.parquet",
            size=len(data),
            modified_at=datetime(2026, 8, 4, tzinfo=UTC),
            snapshot_token="cloud:fixture-etag:",
            **metadata.model_dump(),
        )
    })
    storage = FakeStorage(
        data,
        manifest=results.result_manifest_bytes(manifest),
    )

    monkeypatch.setattr(
        results.ObjectStorage,
        "from_env",
        staticmethod(lambda: storage),
    )
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)
    monkeypatch.setattr(
        results,
        "parquet_result_metadata",
        lambda *_args, **_kwargs: pytest.fail("Parquet body must not be read"),
    )

    files = results.result_files(
        session,
        user.id,
        101,
        "query",
        {"data": "query.parquet"},
    )

    assert files[0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert storage.client.manifest_get_count == 1
    assert storage.client.get_count == 0
    assert storage.client.manifest_put_count == 0


def test_stale_cloud_manifest_falls_back_and_is_replaced(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = successful_run(session)
    data = parquet_bytes()
    stale = results.ResultManifest(files={
        "data": results.ResultManifestEntry(
            filename="query.parquet",
            size=len(data) - 1,
            modified_at=datetime(2026, 8, 4, tzinfo=UTC),
            snapshot_token="cloud:stale-etag:",
            row_count=1,
            columns=[],
            sha256="0" * 64,
        )
    })
    storage = FakeStorage(data, manifest=results.result_manifest_bytes(stale))

    monkeypatch.setattr(
        results.ObjectStorage,
        "from_env",
        staticmethod(lambda: storage),
    )
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)

    files = results.result_files(
        session,
        user.id,
        101,
        "query",
        {"data": "query.parquet"},
    )

    assert files[0]["row_count"] == 2
    assert storage.client.get_count == 1
    assert storage.client.manifest_put_count == 1
    replaced = results.ResultManifest.model_validate_json(storage.client.manifest)
    assert replaced.files["data"].sha256 == hashlib.sha256(data).hexdigest()


def test_cloud_result_listing_only_skips_explicit_legacy_optional_output(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = successful_run(
        session,
        application="backtest",
        requested_outputs=["daily_portfolios", "daily_trading_statistics"],
    )
    missing_filename = "daily_trading_statistics.parquet"

    data = parquet_bytes()

    def create_storage() -> FakeBacktestStorage:
        return FakeBacktestStorage(data, missing_filename)

    monkeypatch.setattr(results.ObjectStorage, "from_env", staticmethod(create_storage))
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)

    files = backtest_result_files(session, user.id, 101)

    assert [file["name"] for file in files] == ["daily_portfolios"]

    missing_filename = "daily_portfolios.parquet"
    with pytest.raises(OSError, match="无法读取对象存储结果"):
        backtest_result_files(session, user.id, 101)


def test_cloud_metadata_cache_is_invalidated_by_etag(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = successful_run(session)
    data = parquet_bytes()
    etag = {"value": "first"}
    instances: list[FakeStorage] = []

    def create_storage() -> FakeStorage:
        storage = FakeStorage(data, etag=etag["value"])
        instances.append(storage)
        return storage

    monkeypatch.setattr(results.ObjectStorage, "from_env", staticmethod(create_storage))
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)

    results.result_files(session, user.id, 101, "query", {"data": "query.parquet"})
    etag["value"] = "second"
    results.result_files(session, user.id, 101, "query", {"data": "query.parquet"})

    assert [storage.client.get_count for storage in instances] == [1, 1]


def test_cloud_metadata_retries_when_object_changes_between_head_and_get(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, _ = successful_run(session)
    replacement = BytesIO()
    pd.DataFrame({"value": [1, 2, 3]}).to_parquet(replacement, index=False)
    storage = FakeStorage(parquet_bytes())
    storage.client.replace_on_first_get = replacement.getvalue()

    monkeypatch.setattr(
        results.ObjectStorage,
        "from_env",
        staticmethod(lambda: storage),
    )
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)

    file = results.result_files(
        session, user.id, 101, "query", {"data": "query.parquet"}
    )[0]

    assert file["row_count"] == 3
    assert file["size"] == len(replacement.getvalue())
    assert file["sha256"] == hashlib.sha256(replacement.getvalue()).hexdigest()
    assert storage.client.precondition_failures == 1


def test_result_listing_checks_user_before_accessing_cloud_storage(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    successful_run(session)
    intruder = User(username="intruder", password_hash="test")
    session.add(intruder)
    session.commit()
    storage_accessed = False

    def create_storage() -> FakeStorage:
        nonlocal storage_accessed
        storage_accessed = True
        return FakeStorage(parquet_bytes())

    monkeypatch.setattr(results.ObjectStorage, "from_env", staticmethod(create_storage))
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)

    with pytest.raises(FileNotFoundError, match="工作流实例不存在"):
        results.result_files(
            session,
            intruder.id,
            101,
            "query",
            {"data": "query.parquet"},
        )

    assert storage_accessed is False


def test_local_result_listing_and_download(
    session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    output_dir = workspace_output_directory("query", WORKSPACE_KEY)
    output_dir.mkdir(parents=True)
    output = output_dir / "query.parquet"
    data = parquet_bytes()
    output.write_bytes(data)
    user, _ = successful_run(session)

    files = results.result_files(
        session,
        user.id,
        101,
        "query",
        {"data": "query.parquet"},
    )
    response = results.result_response(
        session,
        user.id,
        101,
        "data",
        "query",
        {"data": "query.parquet"},
    )

    assert files[0]["size"] == len(data)
    assert files[0]["row_count"] == 2
    assert files[0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert [column["name"] for column in files[0]["columns"]] == [
        "time",
        "code",
        "value",
    ]
    assert str(response.path) == str(output)

    attempt = session.query(WorkflowAttempt).one()
    original_updated_at = attempt.updated_at
    original_events = list(attempt.events)
    assert (output_dir / results.RESULT_MANIFEST_FILENAME).is_file()
    results.clear_result_metadata_cache()
    monkeypatch.setattr(
        results,
        "parquet_result_metadata",
        lambda *_args, **_kwargs: pytest.fail(
            "Backfilled manifest must avoid a second Parquet read"
        ),
    )
    results.result_files(
        session,
        user.id,
        101,
        "query",
        {"data": "query.parquet"},
    )
    session.refresh(attempt)
    assert attempt.updated_at == original_updated_at
    assert attempt.events == original_events


def test_local_result_listing_uses_runtime_manifest_without_reading_parquet(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    output_dir = workspace_output_directory("query", WORKSPACE_KEY)
    output_dir.mkdir(parents=True)
    output = output_dir / "query.parquet"
    data = parquet_bytes()
    output.write_bytes(data)
    snapshot = results.local_result_snapshot(output.stat())
    metadata = results.parquet_result_metadata(BytesIO(data), 0, "data")
    results.write_local_result_manifest(
        output_dir,
        results.ResultManifest(files={
            "data": results.ResultManifestEntry(
                filename="query.parquet",
                size=snapshot.size,
                modified_at=snapshot.modified_at,
                snapshot_token=snapshot.cache_token,
                **metadata.model_dump(),
            )
        }),
    )
    user, _ = successful_run(session)
    results.clear_result_metadata_cache()
    monkeypatch.setattr(
        results,
        "parquet_result_metadata",
        lambda *_args, **_kwargs: pytest.fail("Parquet body must not be read"),
    )

    files = results.result_files(
        session,
        user.id,
        101,
        "query",
        {"data": "query.parquet"},
    )

    assert files[0]["row_count"] == 2
    assert files[0]["sha256"] == hashlib.sha256(data).hexdigest()


def test_unrequested_output_has_a_machine_readable_api_error(
    session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    user, _ = successful_run(session)

    with pytest.raises(results.ResultNotRequestedError) as captured:
        results.result_response(
            session,
            user.id,
            101,
            "diagnostics",
            "query",
            {"data": "query.parquet", "diagnostics": "diagnostics.parquet"},
        )
    with pytest.raises(HTTPException) as mapped:
        raise_api_http_error(captured.value)

    assert mapped.value.status_code == 404
    assert mapped.value.detail == {
        "code": "RESULT_NOT_REQUESTED",
        "message": "工作流未请求结果: diagnostics",
    }


def test_missing_requested_local_output_is_not_reported_as_legacy_absence(
    session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    user, _ = successful_run(session)

    with pytest.raises(OSError, match="成功但缺少已请求结果"):
        results.result_response(
            session,
            user.id,
            101,
            "data",
            "query",
            {"data": "query.parquet"},
        )


def test_local_metadata_cache_is_invalidated_by_file_snapshot(
    session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    output_dir = workspace_output_directory("query", WORKSPACE_KEY)
    output_dir.mkdir(parents=True)
    output = output_dir / "query.parquet"
    output.write_bytes(parquet_bytes())
    user, _ = successful_run(session)

    first = results.result_files(
        session,
        user.id,
        101,
        "query",
        {"data": "query.parquet"},
    )[0]
    replacement = BytesIO()
    pd.DataFrame({"value": [1, 2, 3]}).to_parquet(replacement, index=False)
    output.write_bytes(replacement.getvalue())

    second = results.result_files(
        session,
        user.id,
        101,
        "query",
        {"data": "query.parquet"},
    )[0]

    assert first["row_count"] == 2
    assert second["row_count"] == 3
    assert second["sha256"] == hashlib.sha256(replacement.getvalue()).hexdigest()
    assert second["sha256"] != first["sha256"]


def test_local_metadata_uses_the_same_snapshot_opened_for_reading(
    session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    output_dir = workspace_output_directory("query", WORKSPACE_KEY)
    output_dir.mkdir(parents=True)
    output = output_dir / "query.parquet"
    output.write_bytes(parquet_bytes())
    replacement = BytesIO()
    pd.DataFrame({"value": [1, 2, 3]}).to_parquet(replacement, index=False)
    replacement_path = output_dir / "replacement.parquet"
    replacement_path.write_bytes(replacement.getvalue())
    user, _ = successful_run(session)
    original_open = Path.open
    replaced = False

    def replace_before_open(path: Path, *args, **kwargs):
        nonlocal replaced
        if path == output and not replaced:
            replaced = True
            replacement_path.replace(output)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replace_before_open)

    file = results.result_files(
        session,
        user.id,
        101,
        "query",
        {"data": "query.parquet"},
    )[0]

    assert file["size"] == len(replacement.getvalue())
    assert file["row_count"] == 3
    assert file["sha256"] == hashlib.sha256(replacement.getvalue()).hexdigest()


def test_cloud_result_directory_is_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = FakeStorage(parquet_bytes())
    monkeypatch.setattr(
        results.ObjectStorage,
        "from_env",
        staticmethod(lambda: storage),
    )
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)

    results.delete_result_objects("query", WORKSPACE_KEY)

    assert storage.deleted_key == f"arena-runtime/query/{WORKSPACE_KEY}/output"
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
    def __init__(
        self,
        data: bytes,
        *,
        etag: str = "fixture-etag",
        manifest: bytes | None = None,
    ) -> None:
        self.closed = False
        self.deleted_key: str | None = None
        self.bucket = "arena-bucket"
        self.client = FakeResultClient(data, etag=etag, manifest=manifest)

    @staticmethod
    def object_key(key: str) -> str:
        return f"arena-runtime/{key}"

    def object_info(self, key: str) -> ObjectInfo:
        assert key == f"arena-runtime/query/{WORKSPACE_KEY}/output/query.parquet"
        return ObjectInfo(
            len(self.client.data),
            datetime(2026, 8, 4, tzinfo=UTC),
        )

    def download_url(self, key: str) -> str:
        assert key == f"arena-runtime/query/{WORKSPACE_KEY}/output/query.parquet"
        return "https://storage.example/query.parquet"

    def delete_prefix(self, key: str) -> None:
        self.deleted_key = key

    def close(self) -> None:
        self.closed = True


class FakeBacktestStorage:
    def __init__(self, data: bytes, missing_filename: str) -> None:
        self.missing_filename = missing_filename
        self.bucket = "arena-bucket"
        self.client = FakeResultClient(data, missing_filename)

    @staticmethod
    def object_key(key: str) -> str:
        return f"arena-runtime/{key}"

    def object_info(self, key: str) -> ObjectInfo:
        if key.endswith(f"/{self.missing_filename}"):
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return ObjectInfo(
            len(self.client.data),
            datetime(2026, 8, 4, tzinfo=UTC),
        )

    def close(self) -> None:
        pass


class FakeResultClient:
    def __init__(
        self,
        data: bytes,
        missing_filename: str | None = None,
        *,
        etag: str = "fixture-etag",
        manifest: bytes | None = None,
    ) -> None:
        self.data = data
        self.missing_filename = missing_filename
        self.etag = etag
        self.get_count = 0
        self.precondition_failures = 0
        self.replace_on_first_get: bytes | None = None
        self.manifest = manifest
        self.manifest_get_count = 0
        self.manifest_put_count = 0

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        assert Bucket == "arena-bucket"
        self._raise_if_missing(Key, "HeadObject")
        return {
            "ContentLength": len(self.data),
            "LastModified": datetime(2026, 8, 4, tzinfo=UTC),
            "ETag": f'"{self.etag}"',
        }

    def get_object(
        self,
        *,
        Bucket: str,
        Key: str,
        IfMatch: str | None = None,
        VersionId: str | None = None,
        Range: str | None = None,
    ) -> dict[str, object]:
        assert Bucket == "arena-bucket"
        if Key.endswith(f"/{results.RESULT_MANIFEST_FILENAME}"):
            if self.manifest is None:
                raise ClientError(
                    {
                        "Error": {"Code": "404", "Message": "Not Found"},
                        "ResponseMetadata": {"HTTPStatusCode": 404},
                    },
                    "GetObject",
                )
            assert Range == f"bytes=0-{results.MAX_RESULT_MANIFEST_SIZE}"
            self.manifest_get_count += 1
            return {"Body": BytesIO(self.manifest)}
        self._raise_if_missing(Key, "GetObject")
        if self.replace_on_first_get is not None:
            self.data = self.replace_on_first_get
            self.replace_on_first_get = None
            self.etag = "replacement-etag"
        if IfMatch is not None and IfMatch.strip('"') != self.etag:
            self.precondition_failures += 1
            raise ClientError(
                {
                    "Error": {
                        "Code": "PreconditionFailed",
                        "Message": "object changed",
                    },
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "GetObject",
            )
        assert VersionId is None
        assert Range is None
        self.get_count += 1
        return {"Body": BytesIO(self.data)}

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
    ) -> None:
        assert Bucket == "arena-bucket"
        assert Key.endswith(f"/{results.RESULT_MANIFEST_FILENAME}")
        assert ContentType == "application/json"
        self.manifest = Body
        self.manifest_put_count += 1

    def _raise_if_missing(self, key: str, operation: str) -> None:
        if self.missing_filename and key.endswith(f"/{self.missing_filename}"):
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                operation,
            )


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
