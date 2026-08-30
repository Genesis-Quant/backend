from __future__ import annotations

import json

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from config import ArenaSettings
from core.apps.backtest.models import (
    BacktestProject,
    BacktestResearch,
    BacktestVersion,
)
from core.apps.backtest.services import (
    OUTPUT_FILES as BACKTEST_OUTPUT_FILES,
)
from core.apps.backtest.services import (
    PROJECT_OUTPUTS as BACKTEST_PROJECT_OUTPUTS,
)
from core.apps.backtest.services import (
    create_backtest_version,
    create_backtest_project,
    submit_project_backtest,
)
from core.apps.factor.models import FactorProject, FactorVersion
from core.apps.factor.services import (
    OUTPUT_FILES as FACTOR_OUTPUT_FILES,
)
from core.apps.factor.services import (
    PROJECT_OUTPUTS as FACTOR_PROJECT_OUTPUTS,
)
from core.apps.factor.services import (
    create_factor_project,
    create_factor_version,
    factor_metrics,
    return_growth,
    submit_project_analysis,
)
from core.apps.query.models import QueryProject
from core.apps.query.services import submit_project_query
from core.apps.users.models import User
from core.apps.workflows.artifacts import (
    workspace_input_file,
    workspace_output_directory,
)
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace
from core.apps.workflows.services import (
    AUTO_SAVE_PENDING_STATE,
    BATCH_PENDING_STATE,
    WorkflowExecutionService,
    assign_auto_saved_version_number,
    auto_save_metadata,
    auto_save_workspaces,
    create_workflow_attempt,
    current_workflow_attempt,
    prepare_workspace,
    record_event,
)
from core.database.base import Base
from core.utils.results import OUTPUTS_VALIDATED_EVENT


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            WorkflowWorkspace.__table__,
            QueryProject.__table__,
            FactorProject.__table__,
            BacktestProject.__table__,
            WorkflowAttempt.__table__,
            WorkflowInstance.__table__,
            FactorVersion.__table__,
            BacktestVersion.__table__,
            BacktestResearch.__table__,
        ],
    )
    with Session(engine, expire_on_commit=False) as active_session:
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
        run: WorkflowWorkspace,
        client: object,
    ) -> WorkflowInstance:
        del executor, client
        attempt = session.scalar(
            select(WorkflowAttempt).where(
                WorkflowAttempt.workflow_workspace_id == run.id,
                WorkflowAttempt.is_current.is_(True),
            )
        )
        assert attempt is not None
        workflow_instance_id = next(next_instance_id)
        workflow = WorkflowInstance(
            workflow_instance_id=workflow_instance_id,
            workflow_attempt_id=attempt.id,
            state="SUCCESS",
            state_history=[],
        )
        session.add(workflow)
        record_event(
            attempt,
            OUTPUTS_VALIDATED_EVENT,
            workflow_instance_id=workflow_instance_id,
        )
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
    workspace = WorkflowWorkspace(user_id=user.id, application="query")
    session.add(workspace)
    session.flush()
    project = QueryProject(user_id=user.id, workflow_workspace_id=workspace.id, title="query")
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
    assert session.scalar(select(func.count()).select_from(WorkflowWorkspace).where(WorkflowWorkspace.application == "query")) == 1
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
    attempts = list(session.scalars(select(WorkflowAttempt).order_by(WorkflowAttempt.id)))
    assert [attempt.is_current for attempt in attempts] == [False, False, True]
    assert [
        attempt.input_json["value"]
        for attempt in attempts
    ] == [1, 2, 3]


def test_factor_draft_reuses_workspace_until_version_is_saved(
    session: Session,
    user: User,
    submissions: list[dict[str, object]],
) -> None:
    created = create_factor_project(session, user.id, "factor")
    project = session.get(FactorProject, created["id"])
    assert project is not None
    assert created["draft"]["version"] == 1
    assert created["draft"]["saved"] is False
    assert created["draft"]["state"] == "DRAFT"
    first_payload = {
        "codes_query": None,
        "dataset_query": {
            "start_date": "2020-01-01",
            "end_date": "2020-01-02",
            "lookback": "P30D",
            "codes": ["000001.SZ"],
            "factors": [],
            "derivatives": {
                "factor": {
                    "type": "TS",
                    "op": "unary.pct_change",
                    "fields": {"col": "close_hfq"},
                    "params": {"periods": 20},
                },
                "return": {
                    "type": "TS",
                    "op": "unary.pct_change",
                    "fields": {"col": "close_hfq"},
                    "params": {"periods": 1},
                },
            },
            "filters": [],
        },
        "factor_columns": ["factor"],
        "return_columns": ["return"],
        "return_specs": {"return": {"kind": "simple", "periods": 1}},
        "n_groups": 5,
        "preprocess": True,
        "market_value_column": "circ_mv",
    }
    second_payload = {**first_payload, "market_value_column": "total_mv"}

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
    )

    third = submit_project_analysis(session, user.id, project.id, first_payload)

    assert third.id != first.id
    assert third.workspace_key != original_workspace_key
    versions = list(session.scalars(select(FactorVersion).where(FactorVersion.project_id == project.id).order_by(FactorVersion.version)))
    assert [(version.version, version.saved, version.is_current) for version in versions] == [(1, True, False), (2, False, True)]
    assert versions[0].workflow_workspace_id == first.id
    assert versions[1].workflow_workspace_id == third.id
    assert len(submissions) == 3


def test_backtest_draft_reuses_workspace_until_version_is_saved(
    session: Session,
    user: User,
    submissions: list[dict[str, object]],
) -> None:
    created = create_backtest_project(session, user.id, "backtest")
    project = session.get(BacktestProject, created["id"])
    assert project is not None
    assert created["draft"]["version"] == 1
    assert created["draft"]["saved"] is False
    assert created["draft"]["state"] == "DRAFT"

    first = submit_project_backtest(session, user.id, project.id, {"cash": 1, "annual_trading_days": 252, "risk_free_rate": 0})
    original_workspace_key = first.workspace_key
    second = submit_project_backtest(session, user.id, project.id, {"cash": 2, "annual_trading_days": 252, "risk_free_rate": 0})

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
    )

    third = submit_project_backtest(session, user.id, project.id, {"cash": 3, "annual_trading_days": 252, "risk_free_rate": 0})

    assert third.id != first.id
    assert third.workspace_key != original_workspace_key
    versions = list(session.scalars(select(BacktestVersion).where(BacktestVersion.project_id == project.id).order_by(BacktestVersion.version)))
    assert [(version.version, version.saved, version.is_current) for version in versions] == [(1, True, False), (2, False, True)]
    assert versions[0].workflow_workspace_id == first.id
    assert versions[1].workflow_workspace_id == third.id
    assert len(submissions) == 3


@pytest.mark.parametrize(
    ("application", "project_model", "version_model", "create_project"),
    [
        ("factor", FactorProject, FactorVersion, create_factor_project),
        ("backtest", BacktestProject, BacktestVersion, create_backtest_project),
    ],
)
def test_failed_batch_runs_and_deleted_versions_do_not_consume_or_reuse_numbers(
    session: Session,
    user: User,
    application,
    project_model,
    version_model,
    create_project,
) -> None:
    created = create_project(session, user.id, application)
    project = session.get(project_model, created["id"])
    assert project is not None
    pending = []
    for index in range(3):
        workspace = WorkflowWorkspace(user_id=user.id, application=application)
        session.add(workspace)
        session.flush()
        attempt = create_workflow_attempt(session, workspace, {"index": index}, [])
        workflow = WorkflowInstance(
            workflow_instance_id=2001 + index,
            workflow_attempt_id=attempt.id,
            state="SUCCESS",
            state_history=[],
        )
        version = version_model(
            project_id=project.id,
            workflow_workspace_id=workspace.id,
            version=None,
            saved=False,
            is_current=False,
            parameters={"index": index},
        )
        session.add_all([workflow, version])
        pending.append((version, workflow))
    session.commit()

    for version, workflow in (pending[0], pending[2]):
        assign_auto_saved_version_number(session, version)
        version.workflow_instance_id = workflow.workflow_instance_id
        version.saved = True
        session.flush()
    session.commit()

    versions = list(
        session.scalars(
            select(version_model)
            .where(version_model.project_id == project.id)
            .order_by(version_model.id)
        )
    )
    saved_numbers = sorted(
        version.version for version in versions if version.saved
    )
    current = next(version for version in versions if version.is_current)
    failed = pending[1][0]
    assert saved_numbers == [1, 2]
    assert current.version == 3
    assert failed.version is None

    session.delete(next(version for version in versions if version.version == 2))
    session.flush()
    assign_auto_saved_version_number(session, failed)
    failed.workflow_instance_id = pending[1][1].workflow_instance_id
    failed.saved = True
    session.flush()
    assert failed.version == 3
    assert current.version == 4


def test_batch_client_id_recovers_failed_submission_with_original_input(
    session: Session,
    user: User,
) -> None:
    created = create_factor_project(session, user.id, "factor")
    project = session.get(FactorProject, created["id"])
    assert project is not None
    version = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id))
    assert version is not None
    version.is_current = False
    version.version = None
    version.parameters = {"value": "original"}
    workspace = session.get(WorkflowWorkspace, version.workflow_workspace_id)
    assert workspace is not None
    failed = create_workflow_attempt(
        session,
        workspace,
        {"value": "original"},
        ["information_coefficient"],
        start_parameters={"job_id": "factor:original"},
        submission_state="SUBMIT_FAILED",
    )
    failed.error = "temporary"
    record_event(failed, "AUTO_SAVE_VERSION", client_id="queue-1", project_id=project.id, remark="first")
    session.commit()

    matched, submission_retry_ids, auto_save_retry_ids = auto_save_workspaces(session, FactorVersion, user.id, project.id, {"queue-1"})
    current = current_workflow_attempt(session, workspace.id)

    assert matched == {"queue-1": workspace.id}
    assert submission_retry_ids == [workspace.id]
    assert auto_save_retry_ids == []
    assert current is not None
    assert current.id != failed.id
    assert current.submission_state == BATCH_PENDING_STATE
    assert current.input_json == {"value": "original"}
    assert current.start_parameters["job_id"] == "factor:original"
    assert auto_save_metadata(current)["client_id"] == "queue-1"
    assert version.parameters == {"value": "original"}


def test_batch_client_id_retries_failed_auto_save_without_new_attempt(
    session: Session,
    user: User,
) -> None:
    created = create_factor_project(session, user.id, "factor")
    project = session.get(FactorProject, created["id"])
    assert project is not None
    version = session.scalar(select(FactorVersion).where(FactorVersion.project_id == project.id))
    assert version is not None
    version.is_current = False
    version.version = None
    workspace = session.get(WorkflowWorkspace, version.workflow_workspace_id)
    assert workspace is not None
    failed = create_workflow_attempt(session, workspace, {"value": "original"}, ["group_returns"], submission_state="AUTO_SAVE_FAILED")
    failed.error = "storage unavailable"
    record_event(failed, "AUTO_SAVE_VERSION", client_id="queue-2", project_id=project.id, remark="first")
    session.commit()

    matched, submission_retry_ids, auto_save_retry_ids = auto_save_workspaces(session, FactorVersion, user.id, project.id, {"queue-2"})
    current = current_workflow_attempt(session, workspace.id)

    assert matched == {"queue-2": workspace.id}
    assert submission_retry_ids == []
    assert auto_save_retry_ids == [workspace.id]
    assert current is not None
    assert current.id == failed.id
    assert current.submission_state == AUTO_SAVE_PENDING_STATE
    assert current.error is None
    assert [event["event"] for event in current.events][-1] == "AUTO_VERSION_SAVE_RETRY_QUEUED"


def test_factor_maximum_drawdown_includes_initial_wealth() -> None:
    growth, maximum_drawdown = return_growth(pd.Series([-0.5, 0.1]), "simple")

    assert growth == pytest.approx(0.55)
    assert maximum_drawdown == pytest.approx(0.5)


def test_factor_metrics_expose_return_compounding_contract() -> None:
    parameters = {
        "codes_query": None,
        "dataset_query": {
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "lookback": "P0D",
            "codes": [],
            "factors": ["alpha", "future_return", "circ_mv"],
            "derivatives": {},
            "filters": [],
        },
        "factor_columns": ["alpha"],
        "return_columns": ["future_return"],
        "return_specs": {
            "future_return": {"kind": "log", "periods": 5},
        },
        "n_groups": 5,
        "n_select": 10,
        "preprocess": True,
        "market_value_column": "circ_mv",
    }
    information = pd.DataFrame({
        "time": ["2024-01-02", "2024-01-03"],
        "alpha_future_return_ic": [0.1, 0.2],
        "alpha_future_return_rank_ic": [0.2, 0.3],
    })
    groups = pd.DataFrame({
        "time": ["2024-01-02", "2024-01-03"],
        "alpha_future_return_bottom": [0.01, 0.02],
        "alpha_future_return_top": [0.03, 0.04],
    })

    metric = factor_metrics(parameters, information, groups)["alpha"]["future_return"]

    assert metric["return_kind"] == "log"
    assert metric["return_periods"] == 5
    assert metric["compoundable"] is False
    assert metric["long_short_cumulative_return"] is None


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
    run = WorkflowWorkspace(
        id=1,
        user_id=1,
        application="query",
        workspace_key=workspace_key,
    )
    attempt = WorkflowAttempt(
        id=1,
        workflow_workspace_id=run.id,
        is_current=True,
        submission_state="CREATED",
        input_json={"value": 2},
        start_parameters={},
        requested_outputs=["data"],
        events=[],
    )
    workspace_input_file("query", workspace_key).parent.mkdir(parents=True)
    workspace_input_file("query", workspace_key).write_text(
        json.dumps({"value": 1}),
        encoding="utf-8",
    )

    prepare_workspace(run, attempt, create_directory=False)

    assert deleted == [("query", workspace_key)]
    assert json.loads(
        workspace_input_file("query", workspace_key).read_text(encoding="utf-8")
    ) == {"value": 2}
    assert not workspace_output_directory("query", workspace_key).exists()


def current_instance(session: Session, workspace_id: int) -> WorkflowInstance:
    return session.scalar(
        select(WorkflowInstance)
        .join(WorkflowAttempt, WorkflowAttempt.id == WorkflowInstance.workflow_attempt_id)
        .where(
            WorkflowAttempt.workflow_workspace_id == workspace_id,
            WorkflowAttempt.is_current.is_(True),
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
        path = output_directory / filenames[name]
        if name == "information_coefficient":
            pd.DataFrame({"time": ["2020-01-01", "2020-01-02"], "factor_return_ic": [0.1, 0.2], "factor_return_rank_ic": [0.2, 0.3]}).to_parquet(path)
        elif name == "group_returns":
            pd.DataFrame({"time": ["2020-01-01", "2020-01-02"], "factor_return_group0": [0.01, 0.02], "factor_return_group4": [0.02, 0.04]}).to_parquet(path)
        elif name == "diagnostics":
            pd.DataFrame({
                "time": ["2020-01-01", "2020-01-02"],
                "factor": ["factor", "factor"],
                "return_column": ["return", "return"],
                "universe_count": [2, 2],
                "factor_valid_count": [2, 2],
                "return_valid_count": [2, 2],
                "paired_valid_count": [2, 2],
                "group_valid_count": [2, 2],
                "group_min": [0, 0],
                "group_max": [4, 4],
                "occupied_group_count": [2, 2],
                "min_group_size": [1, 1],
                "max_group_size": [1, 1],
            }).to_parquet(path)
        elif name == "daily_portfolios":
            pd.DataFrame({"tradeDate": ["2020-01-01", "2020-01-02"], "ratio": [0.0, 0.01]}).to_parquet(path)
        else:
            path.write_bytes(b"result")
