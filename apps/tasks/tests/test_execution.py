import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from apps.query.models import QueryTask
from apps.tasks.services import TaskExecutionService, process_parameter
from apps.users.models import User
from apps.utils.results import result_files, result_path
from config.database import Base
from config.dolphinscheduler.errors import DolphinSchedulerError


class FakeClient:
    def __init__(self) -> None:
        self.marker = ""

    def __enter__(self):
        return self

    def __exit__(self, *ignored):
        return None

    def start_process_instance(self, **request):
        self.marker = request["start_params"]["job_id"]

    def process_instances(self, **request):
        return [{"id": 321, "state": "RUNNING_EXECUTION", "globalParams": json.dumps([{"prop": "job_id", "value": self.marker}])}]

    def process_instance(self, project_code, process_instance_id):
        return {"id": process_instance_id, "state": "SUCCESS"}

    def process_instance_tasks(self, **request):
        return [{"id": 987, "name": "query", "state": "SUCCESS"}]


class SynchronizationFailureClient(FakeClient):
    def process_instances(self, **request):
        raise DolphinSchedulerError("temporary read failure")


@pytest.fixture
def task_database(tmp_path):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS arena_backend")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with sessions() as session:
        user = User(username="owner", password_hash="hash")
        other_user = User(username="other", password_hash="hash")
        session.add_all((user, other_user))
        session.commit()
        yield session, user.id, other_user.id, tmp_path
    engine.dispose()


def make_service(tmp_path, client, submission_attempts=2):
    settings = SimpleNamespace(shared_dir=tmp_path)
    return TaskExecutionService(
        "query",
        QueryTask,
        settings=settings,
        client_factory=lambda: client,
        workflow_resolver=lambda application, current_settings: (11, {"code": 22, "name": application}),
        submission_attempts=submission_attempts,
        submission_interval=0.001,
    )


def test_submission_returns_actual_dolphinscheduler_task_id(task_database):
    session, user_id, other_user_id, tmp_path = task_database
    service = make_service(tmp_path, FakeClient())

    task = service.submit(session, user_id, {"dataset_query": {"start_date": "2025-01-01"}}, ["data"])

    assert task.task_id == 987
    assert task.process_instance_id == 321
    assert task.state == "SUCCESS"
    assert json.loads((tmp_path / "query" / str(task.id) / "input.json").read_text(encoding="utf-8")) == {
        "dataset_query": {"start_date": "2025-01-01"},
        "output_dir": "output",
    }


def test_results_require_owned_successful_task(task_database):
    session, user_id, other_user_id, tmp_path = task_database
    service = make_service(tmp_path, FakeClient())
    task = service.submit(session, user_id, {"dataset_query": {}}, ["data"])
    output = tmp_path / "query" / str(task.id) / "output" / "query.parquet"
    output.write_bytes(b"PAR1")

    assert result_files(session, user_id, task.task_id, QueryTask, {"data": "query.parquet"})[0]["name"] == "data"
    assert result_path(session, user_id, task.task_id, "data", QueryTask, {"data": "query.parquet"}) == output
    with pytest.raises(FileNotFoundError):
        result_path(session, other_user_id, task.task_id, "data", QueryTask, {"data": "query.parquet"})

    task.state = "RUNNING_EXECUTION"
    session.commit()
    with pytest.raises(RuntimeError, match="成功后才能获取结果"):
        result_files(session, user_id, task.task_id, QueryTask, {"data": "query.parquet"})


def test_submission_does_not_return_without_task_id(task_database):
    session, user_id, other_user_id, tmp_path = task_database
    service = make_service(tmp_path, SynchronizationFailureClient(), submission_attempts=1)

    with pytest.raises(DolphinSchedulerError, match="未在 .* 秒内创建 task instance"):
        service.submit(session, user_id, {"dataset_query": {}}, ["data"])

    task = session.scalar(select(QueryTask).where(QueryTask.user_id == user_id))
    assert task.state == "SUBMITTED"
    assert task.task_id is None


def test_process_parameter_uses_exact_global_parameter_value():
    instance = {
        "globalParams": json.dumps([
            {"prop": "job_id", "value": "query:10"},
            {"prop": "output", "value": "data"},
        ])
    }

    assert process_parameter(instance, "job_id") == "query:10"
    assert process_parameter(instance, "job_id") != "query:1"
