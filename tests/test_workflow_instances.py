from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from config import ArenaSettings
from core.apps.admin.models import IncrementalWorkflowWorkspace
from core.apps.backtest.models import BacktestVersion
from core.apps.factor.models import FactorVersion
from core.apps.tasks.services import TaskGatewayService
from core.apps.users.models import User
from core.apps.workflows.artifacts import (
    workspace_input_file,
    workspace_output_directory,
)
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace
from core.apps.workflows.services import (
    IncrementalWorkflowExecutionService,
    WorkflowExecutionService,
    WorkflowGatewayService,
    auto_save_metadata,
    create_workflow_attempt,
    current_workflow_attempt,
    resolve_workspace_directory,
    workflow_task_information,
)
from core.apps.workflows.schemas import WorkflowAction
from core.database.base import Base
from core.scheduler.applications.incremental import (
    incremental_message_task_definition,
    incremental_worker_options,
    normalize_incremental_workers,
)
from core.scheduler.errors import DolphinSchedulerError
from core.utils.results import owned_result_workspace


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            WorkflowWorkspace.__table__,
            IncrementalWorkflowWorkspace.__table__,
            WorkflowAttempt.__table__,
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


def create_workflow(session: Session, user_id: int, workflow_instance_id: int) -> tuple[WorkflowWorkspace, WorkflowInstance]:
    run = WorkflowWorkspace(
        user_id=user_id,
        application="incremental",
    )
    session.add(run)
    session.flush()
    session.add(IncrementalWorkflowWorkspace(id=run.id))
    attempt = WorkflowAttempt(
        workflow_workspace_id=run.id,
        is_current=True,
        submission_state="WORKFLOW_CREATED",
        project_code=1,
        workflow_definition_code=2,
        workflow_name="incremental-update",
        input_json={},
        start_parameters={"job_id": f"incremental:{run.id}:1"},
        requested_outputs=[],
        events=[],
    )
    session.add(attempt)
    session.flush()
    workflow = WorkflowInstance(
        workflow_instance_id=workflow_instance_id,
        workflow_attempt_id=attempt.id,
        state="RUNNING_EXECUTION",
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

    found_workflow, _, found_run = WorkflowGatewayService.find_accessible_workflow(session, owner, 321)
    assert found_workflow.workflow_instance_id == workflow.workflow_instance_id
    assert found_run.id == run.id

    _, _, admin_run = WorkflowGatewayService.find_accessible_workflow(session, administrator, 321)
    assert admin_run.id == run.id

    with pytest.raises(FileNotFoundError):
        WorkflowGatewayService.find_accessible_workflow(session, outsider, 321)


def test_one_run_can_track_multiple_workflow_instances(session: Session) -> None:
    owner = create_user(session, "owner")
    run, first = create_workflow(session, owner.id, 100)
    first_attempt = session.get(WorkflowAttempt, first.workflow_attempt_id)
    first_attempt.is_current = False
    second_attempt = WorkflowAttempt(
        workflow_workspace_id=run.id,
        is_current=True,
        submission_state="WORKFLOW_CREATED",
        project_code=1,
        workflow_definition_code=2,
        workflow_name="incremental-update",
        input_json={},
        start_parameters={},
        requested_outputs=[],
        events=[],
    )
    session.add(second_attempt)
    session.flush()
    second = WorkflowInstance(
        workflow_instance_id=101,
        workflow_attempt_id=second_attempt.id,
        state="SUCCESS",
        state_history=[],
    )
    session.add(second)
    session.commit()

    assert session.get(WorkflowAttempt, session.get(WorkflowInstance, 100).workflow_attempt_id).workflow_workspace_id == run.id
    assert session.get(WorkflowAttempt, session.get(WorkflowInstance, 101).workflow_attempt_id).workflow_workspace_id == run.id


def test_synchronization_replaces_current_instance_without_unique_conflict(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "owner")
    run, previous = create_workflow(session, owner.id, 100)
    previous_attempt = session.get(WorkflowAttempt, previous.workflow_attempt_id)
    previous_attempt.is_current = False
    current_attempt = WorkflowAttempt(
        workflow_workspace_id=run.id,
        is_current=True,
        submission_state="SUBMITTED",
        project_code=1,
        workflow_definition_code=2,
        workflow_name="incremental-update",
        input_json={},
        start_parameters={"job_id": previous_attempt.start_parameters["job_id"]},
        requested_outputs=[],
        events=[],
    )
    session.add(current_attempt)
    session.commit()
    executor = WorkflowExecutionService("incremental")
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
    assert workflow.workflow_attempt_id == current_attempt.id
    assert previous_attempt.is_current is False


def test_status_refreshes_requested_workflow_instead_of_current(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "owner")
    run, historical = create_workflow(session, owner.id, 100)
    historical_attempt = session.get(WorkflowAttempt, historical.workflow_attempt_id)
    historical_attempt.is_current = False
    current_attempt = WorkflowAttempt(
        workflow_workspace_id=run.id,
        is_current=True,
        submission_state="WORKFLOW_CREATED",
        project_code=1,
        workflow_definition_code=2,
        workflow_name="incremental-update",
        input_json={},
        start_parameters={},
        requested_outputs=[],
        error="stale synchronization error",
        events=[],
    )
    session.add(current_attempt)
    session.flush()
    current = WorkflowInstance(
        workflow_instance_id=101,
        workflow_attempt_id=current_attempt.id,
        state="RUNNING_EXECUTION",
        state_history=[],
    )
    session.add(current)
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

    monkeypatch.setattr("core.apps.workflows.services.DolphinSchedulerClient", Client)
    monkeypatch.setattr(
        "core.apps.workflows.services.workflow_definition_details",
        lambda definition_code: {
            "taskDefinitionList": [],
            "processTaskRelationList": [],
        },
    )

    information = WorkflowGatewayService().status(session, owner, 100)

    assert set(information) == {"state", "error"}
    assert information["state"] == "SUCCESS"
    assert information["error"] is None
    assert current_attempt.error == "stale synchronization error"
    assert current_attempt.is_current is True

    WorkflowGatewayService().status(session, owner, 101)

    assert current_attempt.error is None


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
    assert result["items"][0]["attempt_count"] == 1
    assert result["items"][0]["current_attempt"]["workflow_instance_id"] == 100
    assert result["items"][0]["current_attempt"]["tasks"] == []
    assert result["items"][0]["current_attempt"]["tasks_error"] == "scheduler unavailable"


def test_workflow_history_includes_submission_failure_without_instance(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "owner")
    run, historical = create_workflow(session, owner.id, 100)
    historical.state = "SUCCESS"
    historical_attempt = session.get(WorkflowAttempt, historical.workflow_attempt_id)
    assert historical_attempt is not None
    historical_attempt.is_current = False
    failed_attempt = WorkflowAttempt(
        workflow_workspace_id=run.id,
        is_current=True,
        submission_state="SUBMIT_FAILED",
        input_json={"workers": ["daily"]},
        start_parameters={},
        requested_outputs=[],
        error="scheduler unavailable",
        events=[{"type": "SUBMIT_FAILED"}],
    )
    session.add(failed_attempt)
    historical_attempt.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    failed_attempt.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    session.commit()

    class UnavailableClient:
        def __enter__(self):
            raise DolphinSchedulerError("scheduler unavailable")

        def __exit__(self, *ignored: object) -> None:
            return None

    monkeypatch.setattr("core.apps.workflows.services.DolphinSchedulerClient", UnavailableClient)

    result = WorkflowGatewayService().list(session, owner, 1, 20, None, "failure")
    history_page = WorkflowGatewayService().attempts(session, owner, run.id, 1, 1)
    next_history_page = WorkflowGatewayService().attempts(session, owner, run.id, 2, 1)
    history = [*history_page["items"], *next_history_page["items"]]
    detail = WorkflowGatewayService().attempt_detail(session, owner, failed_attempt.id)

    assert result["total"] == 1
    assert history_page["total"] == 2
    assert result["items"][0]["current_attempt"]["attempt_id"] == failed_attempt.id
    assert result["items"][0]["current_attempt"]["attempt_number"] == 1
    assert result["items"][0]["current_attempt"]["workflow_instance_id"] is None
    assert result["items"][0]["current_attempt"]["state"] == "SUBMIT_FAILED"
    assert result["items"][0]["current_attempt"]["tasks_error"] is None
    assert [(item["attempt_id"], item["attempt_number"]) for item in history] == [
        (historical_attempt.id, 2),
        (failed_attempt.id, 1),
    ]
    assert history[1]["workflow_instance_id"] is None
    assert detail["attempt_number"] == 1
    assert detail["workflow_instance_id"] is None
    assert detail["payload"]["input_json"] == {"workers": ["daily"]}


def test_auto_save_state_overrides_successful_scheduler_instance(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "owner")
    run, workflow = create_workflow(session, owner.id, 100)
    attempt = session.get(WorkflowAttempt, workflow.workflow_attempt_id)
    assert attempt is not None
    workflow.state = "SUCCESS"
    attempt.submission_state = "AUTO_SAVE_PENDING"
    session.commit()

    class UnavailableClient:
        def __enter__(self):
            raise DolphinSchedulerError("scheduler unavailable")

        def __exit__(self, *ignored: object) -> None:
            return None

    monkeypatch.setattr("core.apps.workflows.services.DolphinSchedulerClient", UnavailableClient)

    pending = WorkflowGatewayService().list(session, owner, 1, 20, None, "active")
    assert pending["total"] == 1
    assert pending["items"][0]["current_attempt"]["state"] == "AUTO_SAVE_PENDING"
    assert WorkflowGatewayService().workspace_status(session, owner, run.id)["state"] == "AUTO_SAVE_PENDING"

    attempt.submission_state = "AUTO_SAVE_FAILED"
    session.commit()
    failed = WorkflowGatewayService().list(session, owner, 1, 20, None, "failure")
    assert failed["total"] == 1
    assert failed["items"][0]["current_attempt"]["state"] == "AUTO_SAVE_FAILED"


def test_retry_failed_preserves_attempt_context_events(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "owner")
    workspace, workflow = create_workflow(session, owner.id, 100)
    workflow.state = "FAILURE"
    previous = current_workflow_attempt(session, workspace.id)
    assert previous is not None
    previous.events = [
        {"event": "AUTO_SAVE_VERSION", "client_id": "queue-1", "project_id": 7, "remark": "retry"},
        {"event": "WORKFLOW_SUBMIT_FAILED", "error": "temporary"},
    ]
    session.commit()

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *ignored: object) -> None:
            return None

        def execute_process_instance(self, *ignored: object):
            return {"success": True}

        def process_instance_tasks(self, **ignored: object):
            return [{"id": 90}]

    monkeypatch.setattr("core.apps.workflows.services.DolphinSchedulerClient", Client)
    monkeypatch.setattr(WorkflowExecutionService, "synchronize", lambda *ignored, **kwargs: None)

    WorkflowGatewayService().control(session, owner, workflow.workflow_instance_id, WorkflowAction.RETRY_FAILED)

    current = current_workflow_attempt(session, workspace.id)
    assert current is not None
    assert current.id == previous.id
    assert current.input_json == previous.input_json
    assert auto_save_metadata(current) == previous.events[0]
    assert current.submission_state == "RETRYING"
    assert [event["event"] for event in current.events] == [
        "AUTO_SAVE_VERSION",
        "WORKFLOW_SUBMIT_FAILED",
        "WORKFLOW_CONTROL_REQUESTED",
    ]
    assert current.events[-1]["previous_task_instance_id"] == 90


def test_submission_retry_reconciles_existing_scheduler_instance_before_start(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    owner = create_user(session, "owner")
    workspace = WorkflowWorkspace(user_id=owner.id, application="query")
    session.add(workspace)
    session.flush()
    marker = f"query:{workspace.id}:previous"
    attempt = create_workflow_attempt(
        session,
        workspace,
        {"value": "original"},
        ["data"],
        start_parameters={"job_id": marker},
        project_code=11,
        workflow_definition_code=22,
        workflow_name="old-query",
        submission_state="QUEUED",
    )
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    output = workspace_output_directory("query", workspace.workspace_key)
    output.mkdir(parents=True)
    stale_result = output / "data.parquet"
    stale_result.write_bytes(b"running")
    session.commit()
    calls = {"definition": 0, "list": 0, "start": 0}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *ignored: object) -> None:
            return None

        def process_instances(self, **ignored: object):
            assert ignored["project_code"] == 11
            assert ignored["process_definition_code"] == 22
            calls["list"] += 1
            return [{"id": 501, "state": "RUNNING_EXECUTION", "globalParams": [{"prop": "job_id", "value": marker}]}]

        def start_process_instance(self, **ignored: object) -> None:
            calls["start"] += 1

    def latest_definition(application: str):
        calls["definition"] += 1
        return 33, {"code": 44, "name": f"new-{application}"}

    monkeypatch.setattr("core.apps.workflows.services.ensure_workflow_definition", latest_definition)
    monkeypatch.setattr("core.apps.workflows.services.DolphinSchedulerClient", Client)

    WorkflowExecutionService("query", submission_interval=0).submit_workspace(session, workspace, create_directory=False, wait_for_workflow=False)

    workflow = session.get(WorkflowInstance, 501)
    assert workflow is not None
    assert workflow.workflow_attempt_id == attempt.id
    assert attempt.submission_state == "WORKFLOW_CREATED"
    assert (attempt.project_code, attempt.workflow_definition_code, attempt.workflow_name) == (11, 22, "old-query")
    assert [event["event"] for event in attempt.events] == ["WORKFLOW_RECONCILED"]
    assert calls == {"definition": 0, "list": 1, "start": 0}
    assert stale_result.read_bytes() == b"running"


def test_submission_retry_does_not_start_when_reconciliation_is_unavailable(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    owner = create_user(session, "owner")
    workspace = WorkflowWorkspace(user_id=owner.id, application="query")
    session.add(workspace)
    session.flush()
    attempt = create_workflow_attempt(
        session,
        workspace,
        {"value": "original"},
        ["data"],
        start_parameters={"job_id": f"query:{workspace.id}:previous"},
        project_code=11,
        workflow_definition_code=22,
        workflow_name="old-query",
        submission_state="QUEUED",
    )
    session.commit()
    starts = 0

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *ignored: object) -> None:
            return None

        def process_instances(self, **ignored: object):
            raise DolphinSchedulerError("scheduler unavailable")

        def start_process_instance(self, **ignored: object) -> None:
            nonlocal starts
            starts += 1

    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr("core.apps.workflows.services.ensure_workflow_definition", lambda application: (1, {"code": 2, "name": application}))
    monkeypatch.setattr("core.apps.workflows.services.DolphinSchedulerClient", Client)

    with pytest.raises(DolphinSchedulerError, match="scheduler unavailable"):
        WorkflowExecutionService("query", submission_attempts=2, submission_interval=0).submit_workspace(
            session,
            workspace,
            create_directory=True,
            wait_for_workflow=False,
        )

    assert starts == 0
    assert attempt.submission_state == "SUBMIT_FAILED"


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

    _, _, _, task = TaskGatewayService.find_accessible_task(session, owner, 100, 200)

    assert task["task_instance_id"] == 200
    assert task["state"] == "FAILURE"


def test_deleting_historical_instance_keeps_current_workspace(session: Session) -> None:
    owner = create_user(session, "owner")
    run, historical = create_workflow(session, owner.id, 100)
    historical.state = "SUCCESS"
    historical_attempt = session.get(WorkflowAttempt, historical.workflow_attempt_id)
    historical_attempt.is_current = False
    current_attempt = WorkflowAttempt(
        workflow_workspace_id=run.id,
        is_current=True,
        submission_state="WORKFLOW_CREATED",
        project_code=1,
        workflow_definition_code=2,
        workflow_name="incremental-update",
        input_json={},
        start_parameters={},
        requested_outputs=[],
        events=[],
    )
    session.add(current_attempt)
    session.flush()
    current = WorkflowInstance(
        workflow_instance_id=101,
        workflow_attempt_id=current_attempt.id,
        state="SUCCESS",
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
    assert session.get(WorkflowWorkspace, run.id) is not None


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
    assert "workflow_workspace_id" in FactorVersion.__table__.columns
    assert "workflow_instance_id" in BacktestVersion.__table__.columns
    assert "workflow_workspace_id" in BacktestVersion.__table__.columns


def test_workflow_directory_uses_uuid_workspace_key(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_key = "3a809554ba8f4c75a5cf46ec441994af"
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    run = WorkflowWorkspace(
        id=1,
        user_id=1,
        application="factor",
        workspace_key=workspace_key,
    )

    assert resolve_workspace_directory(run) == (tmp_path / "factor" / workspace_key).resolve()

    run.workspace_key = "1"
    with pytest.raises(ValueError, match="workspace key"):
        resolve_workspace_directory(run)


def test_incremental_directory_uses_output_inside_uuid_workspace(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_key = "3a809554ba8f4c75a5cf46ec441994af"
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    run = WorkflowWorkspace(
        id=1,
        user_id=1,
        application="incremental",
        workspace_key=workspace_key,
    )

    assert resolve_workspace_directory(run) == (
        tmp_path / "incremental" / workspace_key
    ).resolve()

    run.workspace_key = "other"
    with pytest.raises(ValueError, match="workspace key"):
        resolve_workspace_directory(run)


def test_incremental_submission_passes_optional_worker_output_directory(
    session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "incremental-owner")
    captured: dict[str, object] = {}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *ignored: object) -> None:
            return None

        def start_process_instance(self, **arguments: object) -> None:
            captured.update(arguments)

    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    monkeypatch.setattr(
        "core.apps.workflows.services.ensure_incremental_workflow_definition",
        lambda: (1, {"code": 2, "name": "incremental-update"}),
    )
    monkeypatch.setattr(
        "core.apps.workflows.services.DolphinSchedulerClient",
        Client,
    )
    executor = IncrementalWorkflowExecutionService("incremental")
    monkeypatch.setattr(
        executor,
        "wait_for_workflow_instance",
        lambda *args: None,
    )

    run, _ = executor.submit_incremental(
        session,
        owner.id,
        ["daily", "limit"],
        "console",
        True,
    )
    attempt = session.scalar(select(WorkflowAttempt).where(WorkflowAttempt.workflow_workspace_id == run.id, WorkflowAttempt.is_current.is_(True)))
    assert attempt is not None

    output_dir = workspace_output_directory("incremental", run.workspace_key)
    assert output_dir.is_dir()
    assert output_dir.name == "output"
    assert output_dir.parent.parent == tmp_path / "incremental"
    assert captured["start_params"] == {
        "job_id": f"incremental:{run.id}:{attempt.id}",
        "output_dir": str(output_dir),
        "workers": "daily,limit",
        "channel": "console",
        "overwrite": "true",
    }
    assert attempt.start_parameters == captured["start_params"]


def test_incremental_worker_selection_defaults_to_all_and_rejects_empty() -> None:
    workers = normalize_incremental_workers(None)
    assert workers[0:2] == (
        "daily",
        "fund-daily",
    )
    options = incremental_worker_options()
    assert [option["name"] for option in options] == list(workers)
    assert all(option["description"] for option in options)
    assert normalize_incremental_workers(["stock-daily", "limit"]) == (
        "daily",
        "limit",
    )
    with pytest.raises(ValueError, match="至少指定一个 Worker"):
        normalize_incremental_workers([])


def test_incremental_message_node_uses_generic_message_service() -> None:
    definition = incremental_message_task_definition()

    compile(definition, "incremental-message-node", "exec")
    assert "/opt/arena-runtime/.venv/bin/python" in definition
    assert "os.execv(runtime_python, [runtime_python, *sys.argv])" in definition
    assert "from runtime.messaging import send_message, write_message" in definition
    assert "from runtime.workers.report import build_incremental_message" in definition
    assert 'delivery = send_message(message, "${channel}")' in definition
    assert "messages incremental" not in definition


def test_cloud_workflow_keeps_input_local_and_sends_cloud_value(
    session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(session, "cloud-owner")
    run = WorkflowWorkspace(
        user_id=owner.id,
        application="query",
    )
    session.add(run)
    session.flush()
    attempt = WorkflowAttempt(
        workflow_workspace_id=run.id,
        is_current=True,
        submission_state="CREATED",
        input_json={"dataset_query": {}},
        start_parameters={},
        requested_outputs=["data"],
        events=[],
    )
    session.add(attempt)
    session.flush()
    captured: dict[str, object] = {}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *ignored: object) -> None:
            return None

        def start_process_instance(self, **arguments: object) -> None:
            captured.update(arguments)

    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)
    monkeypatch.setattr(
        "core.apps.workflows.services.ensure_workflow_definition",
        lambda application: (1, {"code": 2, "name": application}),
    )
    monkeypatch.setattr("core.apps.workflows.services.DolphinSchedulerClient", Client)
    executor = WorkflowExecutionService("query")
    monkeypatch.setattr(executor, "wait_for_workflow_instance", lambda *args: None)

    executor.submit_workspace(session, run, create_directory=True)

    input_file = workspace_input_file("query", run.workspace_key)
    input_data = json.loads(input_file.read_text(encoding="utf-8"))
    assert input_data == attempt.input_json
    assert "output_dir" not in input_data
    output_argument = captured["start_params"]["output_dir"]
    assert output_argument.startswith("query/")
    assert output_argument.endswith("/output")
    assert captured["start_params"] == {
        "input_file": str(input_file),
        "output_dir": output_argument,
        "job_id": f"query:{run.id}:{attempt.id}",
        "output": "data",
        "cloud": "true",
    }
    assert attempt.start_parameters == captured["start_params"]


def test_historical_workflow_cannot_read_current_rerun_results(session: Session) -> None:
    owner = create_user(session, "owner")
    run, historical = create_workflow(session, owner.id, 100)
    run.application = "incremental"
    historical.state = "SUCCESS"
    historical_attempt = session.get(WorkflowAttempt, historical.workflow_attempt_id)
    historical_attempt.is_current = False
    current_attempt = WorkflowAttempt(
        workflow_workspace_id=run.id,
        is_current=True,
        submission_state="WORKFLOW_CREATED",
        project_code=1,
        workflow_definition_code=2,
        workflow_name="incremental-update",
        input_json={},
        start_parameters={},
        requested_outputs=[],
        events=[],
    )
    session.add(current_attempt)
    session.flush()
    session.add(
        WorkflowInstance(
            workflow_instance_id=101,
            workflow_attempt_id=current_attempt.id,
            state="SUCCESS",
            state_history=[],
        )
    )
    session.commit()

    with pytest.raises(RuntimeError, match="不是当前实例"):
        owned_result_workspace(session, owner.id, 100, "incremental")
