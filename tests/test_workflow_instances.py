from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from config import DolphinSchedulerSettings
from core.apps.backtest.models import BacktestVersion
from core.apps.factor.models import FactorVersion, FactorWorkflowRun
from core.apps.incremental.models import IncrementalWorkflowRun
from core.apps.tasks.services import TaskGatewayService
from core.apps.users.models import User
from core.apps.workflows.models import WorkflowInstance, WorkflowRun
from core.apps.workflows.services import (
    WorkflowExecutionService,
    WorkflowGatewayService,
    resolve_run_directory,
    workflow_task_information,
)
from core.database.base import Base
from core.scheduler.errors import DolphinSchedulerError
from core.utils.results import owned_result_run


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


def create_user(session: Session, username: str, *, is_admin: bool = False) -> User:
    user = User(username=username, password_hash="test-password-hash", is_admin=is_admin)
    session.add(user)
    session.flush()
    return user


def create_workflow(session: Session, user_id: int, workflow_instance_id: int) -> tuple[IncrementalWorkflowRun, WorkflowInstance]:
    run = IncrementalWorkflowRun(
        user_id=user_id,
        application="incremental",
        submission_state="WORKFLOW_CREATED",
        project_code=1,
        workflow_definition_code=2,
        workflow_name="incremental-update",
        payload={},
        requested_outputs=[],
        events=[],
    )
    session.add(run)
    session.flush()
    workflow = WorkflowInstance(
        workflow_instance_id=workflow_instance_id,
        workflow_run_id=run.id,
        state="RUNNING_EXECUTION",
        is_current=True,
        state_history=[],
    )
    session.add(workflow)
    session.flush()
    return run, workflow


def test_workflow_instance_is_primary_authorization_boundary(session: Session) -> None:
    owner = create_user(session, "owner")
    outsider = create_user(session, "outsider")
    administrator = create_user(session, "administrator", is_admin=True)
    run, workflow = create_workflow(session, owner.id, 321)
    session.commit()

    found_workflow, found_run = WorkflowGatewayService.find_accessible_workflow(session, owner, 321)
    assert found_workflow.workflow_instance_id == workflow.workflow_instance_id
    assert found_run.id == run.id

    _, admin_run = WorkflowGatewayService.find_accessible_workflow(session, administrator, 321)
    assert admin_run.id == run.id

    with pytest.raises(FileNotFoundError):
        WorkflowGatewayService.find_accessible_workflow(session, outsider, 321)


def test_one_run_can_track_multiple_workflow_instances(session: Session) -> None:
    owner = create_user(session, "owner")
    run, first = create_workflow(session, owner.id, 100)
    first.is_current = False
    second = WorkflowInstance(
        workflow_instance_id=101,
        workflow_run_id=run.id,
        state="SUCCESS",
        is_current=True,
        state_history=[],
    )
    session.add(second)
    session.commit()

    assert session.get(WorkflowInstance, 100).workflow_run_id == run.id
    assert session.get(WorkflowInstance, 101).workflow_run_id == run.id


def test_synchronization_replaces_current_instance_without_unique_conflict(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "owner")
    run, previous = create_workflow(session, owner.id, 100)
    run.submission_state = "SUBMITTED"
    session.commit()
    executor = WorkflowExecutionService("incremental", IncrementalWorkflowRun)
    monkeypatch.setattr(
        executor,
        "locate_new_workflow_instance",
        lambda *ignored, **ignored_keywords: {
            "id": 101,
            "state": "RUNNING_EXECUTION",
        },
    )

    workflow = executor.synchronize(session, run, client=object())

    assert workflow is not None
    assert workflow.workflow_instance_id == 101
    assert workflow.is_current is True
    assert session.get(WorkflowInstance, previous.workflow_instance_id).is_current is False


def test_status_refreshes_requested_workflow_instead_of_current(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "owner")
    run, historical = create_workflow(session, owner.id, 100)
    historical.is_current = False
    current = WorkflowInstance(
        workflow_instance_id=101,
        workflow_run_id=run.id,
        state="RUNNING_EXECUTION",
        is_current=True,
        state_history=[],
    )
    session.add(current)
    run.error = "stale synchronization error"
    session.commit()

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *ignored: object) -> None:
            return None

        def process_instance(self, project_code: int, workflow_instance_id: int):
            assert project_code == 1
            assert workflow_instance_id in {100, 101}
            return {"id": workflow_instance_id, "state": "SUCCESS"}

        def process_instance_tasks(self, **ignored: object):
            return []

        def process_definition_details(self, *ignored: object):
            return {"taskDefinitionList": [], "processTaskRelationList": []}

    monkeypatch.setattr("core.apps.workflows.services.DolphinSchedulerClient", Client)

    information = WorkflowGatewayService().status(session, owner, 100)

    assert information["workflow_instance_id"] == 100
    assert information["state"] == "SUCCESS"
    assert information["error"] is None
    assert run.error == "stale synchronization error"
    assert session.get(WorkflowInstance, 101).is_current is True

    WorkflowGatewayService().status(session, owner, 101)

    assert run.error is None


def test_workflow_list_survives_scheduler_login_failure(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "owner")
    create_workflow(session, owner.id, 100)
    session.commit()

    class UnavailableClient:
        def __enter__(self):
            raise DolphinSchedulerError("scheduler unavailable")

        def __exit__(self, *ignored: object) -> None:
            return None

    monkeypatch.setattr("core.apps.workflows.services.DolphinSchedulerClient", UnavailableClient)

    result = WorkflowGatewayService().list(session, owner, 1, 20, None, None)

    assert result["total"] == 1
    assert result["items"][0]["workflow_instance_id"] == 100
    assert result["items"][0]["tasks"] == []
    assert result["items"][0]["tasks_error"] == "scheduler unavailable"


def test_task_authorization_accepts_an_older_retry_attempt(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "owner")
    create_workflow(session, owner.id, 100)
    session.commit()

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *ignored: object) -> None:
            return None

        def process_instance_tasks(self, **ignored: object):
            return [
                {"id": 200, "taskCode": 10, "name": "query", "state": "FAILURE"},
                {"id": 201, "taskCode": 10, "name": "query", "state": "SUCCESS"},
            ]

    monkeypatch.setattr("core.apps.tasks.services.DolphinSchedulerClient", Client)

    _, _, task = TaskGatewayService.find_accessible_task(session, owner, 100, 200)

    assert task["task_instance_id"] == 200
    assert task["state"] == "FAILURE"


def test_deleting_historical_instance_keeps_current_run(session: Session) -> None:
    owner = create_user(session, "owner")
    run, historical = create_workflow(session, owner.id, 100)
    historical.state = "SUCCESS"
    historical.is_current = False
    current = WorkflowInstance(
        workflow_instance_id=101,
        workflow_run_id=run.id,
        state="SUCCESS",
        is_current=True,
        state_history=[],
    )
    session.add(current)
    session.commit()

    with pytest.raises(RuntimeError, match="历史实例"):
        WorkflowGatewayService().delete(session, owner, 101)

    result = WorkflowGatewayService().delete(session, owner, 100)

    assert result["workflow_instance_id"] == 100
    assert session.get(WorkflowInstance, 100) is None
    assert session.get(WorkflowInstance, 101) is not None
    assert session.get(WorkflowRun, run.id) is not None


def test_live_task_information_supports_multiple_tasks_and_latest_attempt() -> None:
    definition = {
        "taskDefinitionList": [
            {"code": 10, "name": "query", "taskType": "SHELL"},
            {"code": 20, "name": "factor", "taskType": "SHELL"},
        ],
        "processTaskRelationList": [
            {"preTaskCode": 10, "postTaskCode": 20},
        ],
    }
    tasks = workflow_task_information(
        [
            {"id": 100, "taskCode": 10, "name": "query", "state": "FAILURE"},
            {"id": 101, "taskCode": 10, "name": "query", "state": "SUCCESS"},
        ],
        definition,
    )

    assert [task["task_code"] for task in tasks] == [10, 20]
    assert tasks[0]["task_instance_id"] == 101
    assert tasks[0]["state"] == "SUCCESS"
    assert tasks[1]["task_instance_id"] is None
    assert tasks[1]["state"] == "WAITING"


def test_task_instances_are_not_persisted() -> None:
    assert "workflow_task_instances" not in Base.metadata.tables
    assert "workflow_task_definitions" not in Base.metadata.tables
    assert "workflow_instance_id" in FactorVersion.__table__.columns
    assert "workflow_run_id" not in FactorVersion.__table__.columns
    assert "workflow_instance_id" in BacktestVersion.__table__.columns
    assert "workflow_run_id" not in BacktestVersion.__table__.columns


def test_workflow_directory_uses_uuid_workspace_key(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_key = "3a809554ba8f4c75a5cf46ec441994af"
    monkeypatch.setattr(DolphinSchedulerSettings, "SHARED_DIR", tmp_path)
    run = FactorWorkflowRun(
        id=1,
        user_id=1,
        application="factor",
        submission_state="CREATED",
        payload={},
        requested_outputs=[],
        events=[],
        input_file=str(tmp_path / "factor" / workspace_key / "input.json"),
    )

    assert resolve_run_directory(run) == (tmp_path / "factor" / workspace_key).resolve()

    run.input_file = str(tmp_path / "factor" / "1" / "input.json")
    with pytest.raises(RuntimeError, match="workspace key"):
        resolve_run_directory(run)


def test_historical_workflow_cannot_read_current_rerun_results(session: Session) -> None:
    owner = create_user(session, "owner")
    run, historical = create_workflow(session, owner.id, 100)
    run.application = "incremental"
    historical.state = "SUCCESS"
    historical.is_current = False
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

    with pytest.raises(RuntimeError, match="不是当前实例"):
        owned_result_run(session, owner.id, 100, "incremental")
