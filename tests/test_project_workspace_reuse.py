from __future__ import annotations

import json
from copy import deepcopy

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from config import ArenaSettings
from core.apps.backtest.models import (
    BacktestProject,
    BacktestVersion,
    BacktestWorkflowRun,
)
from core.apps.backtest.services import (
    OUTPUT_FILES as BACKTEST_OUTPUT_FILES,
)
from core.apps.backtest.services import (
    PROJECT_OUTPUTS as BACKTEST_PROJECT_OUTPUTS,
)
from core.apps.backtest.services import (
    create_backtest_version,
    submit_project_backtest,
)
from core.apps.factor.models import FactorProject, FactorVersion, FactorWorkflowRun
from core.apps.factor.services import (
    OUTPUT_FILES as FACTOR_OUTPUT_FILES,
)
from core.apps.factor.services import (
    PROJECT_OUTPUTS as FACTOR_PROJECT_OUTPUTS,
)
from core.apps.factor.services import (
    create_factor_version,
    submit_project_analysis,
)
from core.apps.query.models import QueryProject, QueryWorkflowRun
from core.apps.query.services import submit_project_query
from core.apps.users.models import User
from core.apps.workflows.artifacts import (
    workspace_input_file,
    workspace_output_directory,
)
from core.apps.workflows.models import WorkflowInstance, WorkflowRun
from core.apps.workflows.services import (
    WorkflowExecutionService,
    prepare_run_workspace,
)
from core.database.base import Base


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            QueryProject.__table__,
            FactorProject.__table__,
            BacktestProject.__table__,
            WorkflowRun.__table__,
            QueryWorkflowRun.__table__,
            FactorWorkflowRun.__table__,
            BacktestWorkflowRun.__table__,
            WorkflowInstance.__table__,
            FactorVersion.__table__,
            BacktestVersion.__table__,
        ],
    )
    with Session(engine) as active_session:
        yield active_session


@pytest.fixture
def user(session: Session) -> User:
    owner = User(username="owner", password_hash="test")
    session.add(owner)
    session.commit()
    return owner


@pytest.fixture
def submissions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> list[dict[str, object]]:
    started: list[dict[str, object]] = []
    next_instance_id = iter(range(1001, 1100))

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *ignored: object) -> None:
            return None

        def start_process_instance(self, **arguments: object) -> None:
            started.append(arguments)

    def record_successful_instance(
        executor: WorkflowExecutionService,
        session: Session,
        run: WorkflowRun,
        client: object,
    ) -> WorkflowInstance:
        del executor, client
        workflow = WorkflowInstance(
            workflow_instance_id=next(next_instance_id),
            workflow_run_id=run.id,
            state="SUCCESS",
            is_current=True,
            state_history=[],
            payload_snapshot=deepcopy(run.payload),
            requested_outputs_snapshot=list(run.requested_outputs),
        )
        session.add(workflow)
        session.commit()
        return workflow

    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", False)
    monkeypatch.setattr(
        "core.apps.workflows.services.ensure_workflow_definition",
        lambda application: (1, {"code": 2, "name": application}),
    )
    monkeypatch.setattr(
        "core.apps.workflows.services.DolphinSchedulerClient",
        Client,
    )
    monkeypatch.setattr(
        WorkflowExecutionService,
        "wait_for_workflow_instance",
        record_successful_instance,
    )
    return started


def test_query_project_reuses_one_workspace(
    session: Session,
    user: User,
    submissions: list[dict[str, object]],
) -> None:
    project = QueryProject(user_id=user.id, title="query")
    session.add(project)
    session.commit()

    first = submit_project_query(session, user.id, project.id, {"value": 1})
    workspace_key = first.workspace_key
    stale_output = workspace_output_directory("query", workspace_key) / "query.parquet"
    stale_output.write_bytes(b"stale")

    second = submit_project_query(session, user.id, project.id, {"value": 2})
    third = submit_project_query(session, user.id, project.id, {"value": 3})

    assert second.id == first.id == third.id
    assert second.workspace_key == workspace_key == third.workspace_key
    assert session.scalar(select(func.count()).select_from(QueryWorkflowRun)) == 1
    assert len(submissions) == 3
    assert not stale_output.exists()
    assert json.loads(
        workspace_input_file("query", workspace_key).read_text(encoding="utf-8")
    ) == {"value": 3}

    workflows = list(
        session.scalars(
            select(WorkflowInstance).order_by(
                WorkflowInstance.workflow_instance_id
            )
        )
    )
    assert len(workflows) == 3
    assert [workflow.is_current for workflow in workflows] == [False, False, True]
    assert [
        workflow.payload_snapshot["input_json"]["value"]
        for workflow in workflows
    ] == [1, 2, 3]


def test_factor_draft_reuses_workspace_until_version_is_saved(
    session: Session,
    user: User,
    submissions: list[dict[str, object]],
) -> None:
    project = FactorProject(user_id=user.id, title="factor")
    session.add(project)
    session.commit()
    first_payload = {"factor_columns": ["factor"], "return_columns": ["return"]}
    second_payload = {
        "factor_columns": ["factor"],
        "return_columns": ["return"],
        "preprocess": False,
    }

    first = submit_project_analysis(session, user.id, project.id, first_payload)
    original_workspace_key = first.workspace_key
    second = submit_project_analysis(session, user.id, project.id, second_payload)

    assert second.id == first.id
    assert second.workspace_key == original_workspace_key
    write_requested_outputs(
        "factor",
        original_workspace_key,
        FACTOR_PROJECT_OUTPUTS,
        FACTOR_OUTPUT_FILES,
    )
    current = current_instance(session, second.id)
    create_factor_version(
        session,
        user.id,
        project.id,
        current.workflow_instance_id,
        "v1",
        {"factor": {"return": {"ic": 0.1}}},
    )

    third = submit_project_analysis(session, user.id, project.id, first_payload)

    assert third.id != first.id
    assert third.workspace_key != original_workspace_key
    assert session.get(FactorWorkflowRun, first.id).saved is True
    assert session.get(FactorWorkflowRun, third.id).saved is False
    assert len(submissions) == 3


def test_backtest_draft_reuses_workspace_until_version_is_saved(
    session: Session,
    user: User,
    submissions: list[dict[str, object]],
) -> None:
    project = BacktestProject(user_id=user.id, title="backtest")
    session.add(project)
    session.commit()

    first = submit_project_backtest(session, user.id, project.id, {"cash": 1})
    original_workspace_key = first.workspace_key
    second = submit_project_backtest(session, user.id, project.id, {"cash": 2})

    assert second.id == first.id
    assert second.workspace_key == original_workspace_key
    write_requested_outputs(
        "backtest",
        original_workspace_key,
        BACKTEST_PROJECT_OUTPUTS,
        BACKTEST_OUTPUT_FILES,
    )
    current = current_instance(session, second.id)
    create_backtest_version(
        session,
        user.id,
        project.id,
        current.workflow_instance_id,
        "v1",
        {"sharpe": 1.0},
    )

    third = submit_project_backtest(session, user.id, project.id, {"cash": 3})

    assert third.id != first.id
    assert third.workspace_key != original_workspace_key
    assert session.get(BacktestWorkflowRun, first.id).saved is True
    assert session.get(BacktestWorkflowRun, third.id).saved is False
    assert len(submissions) == 3


def test_reusing_cloud_workspace_clears_existing_output_prefix(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_key = "3a809554ba8f4c75a5cf46ec441994af"
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(ArenaSettings, "SHARED_DIR", tmp_path)
    monkeypatch.setattr(ArenaSettings, "SHARED_CLOUD", True)
    monkeypatch.setattr(
        "core.apps.workflows.services.delete_result_objects",
        lambda application, key: deleted.append((application, key)),
    )
    run = QueryWorkflowRun(
        id=1,
        user_id=1,
        application="query",
        workspace_key=workspace_key,
        payload={"start_parameters": {}, "input_json": {"value": 2}},
        requested_outputs=["data"],
        submission_state="CREATED",
        events=[],
    )
    workspace_input_file("query", workspace_key).parent.mkdir(parents=True)
    workspace_input_file("query", workspace_key).write_text(
        json.dumps({"value": 1}),
        encoding="utf-8",
    )

    prepare_run_workspace(run, create_directory=False)

    assert deleted == [("query", workspace_key)]
    assert json.loads(
        workspace_input_file("query", workspace_key).read_text(encoding="utf-8")
    ) == {"value": 2}
    assert not workspace_output_directory("query", workspace_key).exists()


def current_instance(session: Session, run_id: int) -> WorkflowInstance:
    return session.scalar(
        select(WorkflowInstance).where(
            WorkflowInstance.workflow_run_id == run_id,
            WorkflowInstance.is_current.is_(True),
        )
    )


def write_requested_outputs(
    application: str,
    workspace_key: str,
    requested: list[str],
    filenames: dict[str, str],
) -> None:
    output_directory = workspace_output_directory(application, workspace_key)
    for name in requested:
        (output_directory / filenames[name]).write_bytes(b"result")
