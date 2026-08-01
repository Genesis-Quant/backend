from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apps.query.models import QueryTask
from apps.tasks.schemas import TaskAction
from apps.tasks.services import TaskGatewayService
from apps.users.models import User
from config.database import Base


class FakeClient:
    def __init__(self) -> None:
        self.task_actions = []
        self.process_actions = []
        self.task_instances = [{
            "id": 42,
            "name": "query",
            "state": "RUNNING_EXECUTION",
            "host": "worker:1234",
            "retryTimes": 1,
            "maxRetryTimes": 2,
            "startTime": "2026-08-01 15:00:00",
            "endTime": None,
        }]

    def __enter__(self):
        return self

    def __exit__(self, *ignored):
        return None

    def process_instance(self, project_code, process_instance_id):
        return {"id": process_instance_id, "state": "RUNNING_EXECUTION"}

    def process_instance_tasks(self, **request):
        return self.task_instances

    def task_log(self, **request):
        return {
            "skip_line_num": request["skip_line_num"],
            "returned_lines": 10,
            "next_line_num": request["skip_line_num"] + 10,
            "has_more": True,
            "message": "running\n",
        }

    def execute_task_instance(self, project_code, task_instance_id, action):
        self.task_actions.append((project_code, task_instance_id, action))
        return 1001

    def execute_process_instance(self, project_code, process_instance_id, execute_type):
        self.process_actions.append((project_code, process_instance_id, execute_type))
        return 1002


@pytest.fixture
def task_gateway(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS arena_backend")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    client = FakeClient()
    settings = SimpleNamespace(shared_dir=tmp_path, poll_batch_size=100, poll_interval_seconds=0.01)
    service = TaskGatewayService(settings, client_factory=lambda: client, engine=engine, session_factory=sessions)
    with sessions() as session:
        owner = User(username="owner", password_hash="hash")
        other = User(username="other", password_hash="hash")
        session.add_all((owner, other))
        session.commit()
        task_dir = tmp_path / "query" / "1"
        (task_dir / "output").mkdir(parents=True)
        (task_dir / "input.json").write_text("{}", encoding="utf-8")
        task = QueryTask(
            user_id=owner.id,
            task_id=42,
            project_code=11,
            process_definition_code=12,
            process_instance_id=13,
            workflow_name="query",
            state="RUNNING_EXECUTION",
            process_state="RUNNING_EXECUTION",
            payload={},
            requested_outputs=["data"],
            input_file=str(task_dir / "input.json"),
            output_dir=str(task_dir / "output"),
            task_id_history=[],
            process_instance_history=[],
            state_history=[],
            events=[],
        )
        session.add(task)
        session.commit()
        yield service, session, client, owner.id, other.id, task
    engine.dispose()


def test_gateway_authenticates_task_id_and_returns_scheduler_status(task_gateway):
    service, session, client, owner_id, other_id, task = task_gateway

    status = service.status(session, owner_id, 42)

    assert status["application"] == "query"
    assert status["task_id"] == 42
    assert status["host"] == "worker:1234"
    assert status["retry_times"] == 1
    with pytest.raises(FileNotFoundError):
        service.status(session, other_id, 42)


def test_gateway_log_uses_absolute_cursor_and_controls_current_task(task_gateway):
    service, session, client, owner_id, other_id, task = task_gateway

    log = service.log(session, owner_id, 42, 50, 10)
    controlled = service.control(session, owner_id, 42, TaskAction.STOP)

    assert log["next_line_num"] == 60
    assert controlled["scheduler_submission"] == 1001
    assert client.task_actions == [(11, 42, "stop")]


def test_rerun_keeps_old_task_id_authorized(task_gateway):
    service, session, client, owner_id, other_id, task = task_gateway
    task.state = "SUCCESS"
    task.process_state = "SUCCESS"
    session.commit()

    result = service.control(session, owner_id, 42, TaskAction.RERUN)

    assert result["task"]["task_id"] is None
    assert result["task"]["task_id_history"] == [42]
    assert result["task"]["process_instance_id"] == 13
    assert client.process_actions == [(11, 13, "REPEAT_RUNNING")]
    assert service.find_owned_task(session, owner_id, 42)[1].id == task.id

    service.status(session, owner_id, 42)
    session.refresh(task)
    assert task.task_id is None

    client.task_instances.append({"id": 43, "name": "query", "state": "SUCCESS"})
    status = service.status(session, owner_id, 42)
    assert status["task_id"] == 43
    assert status["process_instance_id"] == 13


def test_background_polling_updates_nonterminal_tasks(task_gateway):
    service, session, client, owner_id, other_id, task = task_gateway

    assert service.poll_once() == 1
    session.refresh(task)
    assert task.last_synced_at is not None


def test_delete_removes_terminal_record_and_shared_directory(task_gateway):
    service, session, client, owner_id, other_id, task = task_gateway
    task.state = "SUCCESS"
    session.commit()
    task_dir = service.settings.shared_dir / "query" / str(task.id)

    deleted = service.delete(session, owner_id, 42)

    assert deleted == {"application": "query", "record_id": task.id, "task_id": 42}
    assert session.get(QueryTask, task.id) is None
    assert not task_dir.exists()
