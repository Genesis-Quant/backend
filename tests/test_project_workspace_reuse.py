from __future__ import annotations

import json

import pandas as pd
import pytest
from pydantic import ValidationError
from runtime import OptimizationSettings
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from config import ArenaSettings
from core.apps.backtest.models import (
    BacktestOptimization,
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
    create_backtest_optimization,
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
from core.utils.dsl_source import (
    BacktestApplicationRequest,
    FactorAnalysisApplicationRequest,
)
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
            BacktestOptimization.__table__,
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


CALLBACKS = {
    "initialize": "def initialize(mutable context) { return NULL }",
    "beforeTrading": "def beforeTrading(mutable context) { return NULL }",
    "onBar": "def onBar(mutable context, message, indicator) { return NULL }",
    "onSnapshot": "def onSnapshot(mutable context, message, indicator) { return NULL }",
    "onOrder": "def onOrder(mutable context, event) { return NULL }",
    "onTrade": "def onTrade(mutable context, event) { return NULL }",
    "afterTrading": "def afterTrading(mutable context) { return NULL }",
    "finalize": "def finalize(mutable context) { return NULL }",
}


def source_query(
    *,
    factors: list[str] | None = None,
    derivatives: dict | None = None,
    codes: list[str] | None = None,
) -> dict:
    document = {
        "factors": factors or [],
        "derivatives": derivatives or {},
        "filters": [],
    }
    return {
        "start_date": "2020-01-01",
        "end_date": "2020-01-02",
        "lookback": "P30D",
        "codes": ["000001.SZ"] if codes is None else codes,
        **document,
        "dsl_source": {
            "language": "json",
            "json_source": json.dumps(document),
            "python_source": "inactive",
        },
    }


def query_payload(name: str) -> dict:
    return {"dataset_query": source_query(factors=[name])}


def factor_parameters(
    market_value_column: str = "circ_mv",
) -> FactorAnalysisApplicationRequest:
    factor = {
        "type": "TS",
        "op": "unary.pct_change",
        "fields": {"col": "close_hfq"},
        "params": {"periods": 20},
    }
    return_node = {
        "type": "TS",
        "op": "unary.pct_change",
        "fields": {"col": "close_hfq"},
        "params": {"periods": 1},
    }
    dataset = source_query(
        derivatives={"factor": factor},
        codes=[],
    )
    dataset["derivatives"] = {"factor": factor, "return": return_node}
    return FactorAnalysisApplicationRequest.model_validate({
        "codes_query": None,
        "dataset_query": dataset,
        "factor_columns": ["factor"],
        "return_columns": ["return"],
        "return_specs": {"return": {"kind": "simple", "periods": 1}},
        "n_groups": 5,
        "n_select": 10,
        "preprocess": True,
        "market_value_column": market_value_column,
        "industry_column": "industry",
    })


def backtest_parameters(cash: float = 1_000_000) -> BacktestApplicationRequest:
    return BacktestApplicationRequest.model_validate({
        "config": {
            "cash": cash,
            "commission": 0.0003,
            "tax": 0.001,
            "enableMinimumPerTransactionFee": True,
        },
        "params": {},
        "codes_query": None,
        "dataset_query": source_query(factors=["close"]),
        "adj": None,
        "annual_trading_days": 252,
        "risk_free_rate": 0.0,
        "utils": "",
        "callbacks": CALLBACKS,
    })


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

    payloads = [query_payload(name) for name in ("open", "high", "low")]
    first = submit_project_query(session, user.id, project.id, payloads[0])
    workspace_key = first.workspace_key
    stale_output = workspace_output_directory("query", workspace_key) / "query.parquet"
    stale_output.write_bytes(b"stale")

    second = submit_project_query(session, user.id, project.id, payloads[1])
    third = submit_project_query(session, user.id, project.id, payloads[2])

    assert second.id == first.id == third.id
    assert second.workspace_key == workspace_key == third.workspace_key
    assert session.scalar(select(func.count()).select_from(WorkflowWorkspace).where(WorkflowWorkspace.application == "query")) == 1
    assert len(submissions) == 3
    assert not stale_output.exists()
    assert json.loads(
        workspace_input_file("query", workspace_key).read_text(encoding="utf-8")
    )["dataset_query"]["factors"] == ["low"]

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
        attempt.input_json
        for attempt in attempts
    ] == payloads


def test_factor_draft_reuses_workspace_until_version_is_saved(
    session: Session,
    user: User,
    submissions: list[dict[str, object]],
) -> None:
    initial_parameters = factor_parameters()
    created = create_factor_project(
        session,
        user.id,
        "factor",
        initial_parameters,
    )
    project = session.get(FactorProject, created["id"])
    assert project is not None
    assert created["draft"]["version"] == 1
    assert created["draft"]["saved"] is False
    assert created["draft"]["state"] == "DRAFT"
    first_payload = initial_parameters.stored_payload()
    second_payload = factor_parameters("total_mv").stored_payload()

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
    assert versions[0].metrics["factor"]["return"]["average_turnover"] == pytest.approx(2 / 7)
    assert versions[1].workflow_workspace_id == third.id
    assert len(submissions) == 3


def test_backtest_draft_reuses_workspace_until_version_is_saved(
    session: Session,
    user: User,
    submissions: list[dict[str, object]],
) -> None:
    initial_parameters = backtest_parameters()
    created = create_backtest_project(
        session,
        user.id,
        "backtest",
        initial_parameters,
    )
    project = session.get(BacktestProject, created["id"])
    assert project is not None
    assert created["draft"]["version"] == 1
    assert created["draft"]["saved"] is False
    assert created["draft"]["state"] == "DRAFT"

    first = submit_project_backtest(
        session,
        user.id,
        project.id,
        backtest_parameters(1).stored_payload(),
    )
    original_workspace_key = first.workspace_key
    second = submit_project_backtest(
        session,
        user.id,
        project.id,
        backtest_parameters(2).stored_payload(),
    )

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

    third = submit_project_backtest(
        session,
        user.id,
        project.id,
        backtest_parameters(3).stored_payload(),
    )

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
    initial_parameters = (
        factor_parameters()
        if application == "factor"
        else backtest_parameters()
    )
    created = create_project(
        session,
        user.id,
        application,
        initial_parameters,
    )
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
    created = create_factor_project(
        session,
        user.id,
        "factor",
        factor_parameters(),
    )
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

    matched, submission_retry_ids, auto_save_retry_ids = auto_save_workspaces(session, FactorVersion, user.id, project.id, {"queue-1": ({"value": "original"}, "first")})
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
    created = create_factor_project(
        session,
        user.id,
        "factor",
        factor_parameters(),
    )
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

    matched, submission_retry_ids, auto_save_retry_ids = auto_save_workspaces(session, FactorVersion, user.id, project.id, {"queue-2": ({"value": "original"}, "first")})
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


@pytest.mark.parametrize("version_model,create,parameters", [
    (FactorVersion, create_factor_project, factor_parameters),
    (BacktestVersion, create_backtest_project, backtest_parameters),
])
@pytest.mark.parametrize("state", ["SUBMIT_FAILED", "AUTO_SAVE_FAILED", "AUTO_SAVE_COMPLETE", "SUBMITTED"])
@pytest.mark.parametrize("changed", ["parameters", "remark"])
def test_batch_client_id_rejects_changed_request_before_retry(
    session, user, version_model, create, parameters, state, changed,
):
    created = create(session, user.id, "batch", parameters())
    version = session.scalar(select(version_model).where(version_model.project_id == created["id"]))
    workspace = session.get(WorkflowWorkspace, version.workflow_workspace_id)
    attempt = create_workflow_attempt(session, workspace, {"value": 1}, [], submission_state=state)
    record_event(attempt, "AUTO_SAVE_VERSION", client_id="same-id", project_id=created["id"], remark="original")
    session.commit()
    before = session.scalar(select(func.count()).select_from(WorkflowAttempt))
    parameters = {"value": True} if changed == "parameters" else {"value": 1}
    remark = "changed" if changed == "remark" else "original"

    with pytest.raises(ValueError, match="client_id.*已绑定不同"):
        auto_save_workspaces(session, version_model, user.id, created["id"], {"same-id": (parameters, remark)})

    assert session.scalar(select(func.count()).select_from(WorkflowAttempt)) == before
    assert current_workflow_attempt(session, workspace.id).id == attempt.id
    assert attempt.submission_state == state


def test_batch_client_id_reuses_identical_payload_with_reordered_keys(session, user):
    created = create_factor_project(session, user.id, "batch", factor_parameters())
    version = session.scalar(select(FactorVersion).where(FactorVersion.project_id == created["id"]))
    workspace = session.get(WorkflowWorkspace, version.workflow_workspace_id)
    attempt = create_workflow_attempt(session, workspace, {"a": 1, "b": 2}, [], submission_state="AUTO_SAVE_COMPLETE")
    record_event(attempt, "AUTO_SAVE_VERSION", client_id="same", project_id=created["id"], remark="original")
    session.commit()
    before = session.scalar(select(func.count()).select_from(WorkflowAttempt))

    matched, submissions, saves = auto_save_workspaces(
        session, FactorVersion, user.id, created["id"], {"same": ({"b": 2, "a": 1}, "original")},
    )

    assert matched == {"same": workspace.id}
    assert submissions == saves == []
    assert session.scalar(select(func.count()).select_from(WorkflowAttempt)) == before


def test_batch_conflict_does_not_mutate_other_retry_candidates(session, user):
    created = create_factor_project(session, user.id, "batch", factor_parameters())
    attempts = []
    for number in range(2):
        workspace = WorkflowWorkspace(user_id=user.id, application="factor")
        session.add(workspace)
        session.flush()
        session.add(FactorVersion(project_id=created["id"], workflow_workspace_id=workspace.id,
                                  version=None, saved=False, is_current=False, parameters={}))
        attempt = create_workflow_attempt(session, workspace, {"value": number}, [], submission_state="AUTO_SAVE_FAILED")
        record_event(attempt, "AUTO_SAVE_VERSION", client_id=f"item-{number}", project_id=created["id"], remark="")
        attempts.append(attempt)
    session.commit()

    with pytest.raises(ValueError, match="client_id.*已绑定不同"):
        auto_save_workspaces(session, FactorVersion, user.id, created["id"], {
            "item-0": ({"value": 0}, ""),
            "item-1": ({"value": 9}, ""),
        })

    assert all(attempt.submission_state == "AUTO_SAVE_FAILED" for attempt in attempts)


def test_invalid_optimization_does_not_create_workspace_or_attempt(session, user, monkeypatch):
    created = create_backtest_project(session, user.id, "optimization", backtest_parameters())
    version = session.scalar(select(BacktestVersion).where(BacktestVersion.project_id == created["id"]))
    monkeypatch.setattr("core.apps.backtest.services.owned_batch_version", lambda *args: version)
    counts = {model: session.scalar(select(func.count()).select_from(model)) for model in (
        WorkflowWorkspace, WorkflowAttempt, BacktestOptimization,
    )}
    settings = OptimizationSettings(
        parameter_space={"missing": [1, 2]}, algorithms=["random_search"],
        start_date="2025-01-01", end_date="2025-03-01",
        lookback_period="1M", holding_period="2W",
    )

    with pytest.raises(ValidationError, match="parameter_space 只能选择 params 已定义"):
        create_backtest_optimization(session, user, created["id"], 1, settings)

    for model, count in counts.items():
        assert session.scalar(select(func.count()).select_from(model)) == count


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
        input_json=query_payload("high"),
        start_parameters={},
        requested_outputs=["data"],
        events=[],
    )
    workspace_input_file("query", workspace_key).parent.mkdir(parents=True)
    workspace_input_file("query", workspace_key).write_text(
        json.dumps(query_payload("open")),
        encoding="utf-8",
    )

    prepare_workspace(run, attempt, create_directory=False)

    assert deleted == [("query", workspace_key)]
    assert json.loads(
        workspace_input_file("query", workspace_key).read_text(encoding="utf-8")
    )["dataset_query"]["factors"] == ["high"]
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
        elif name == "execution_statistics":
            pd.DataFrame({
                "time": ["2020-01-01", "2020-01-02"],
                "source_count": [300, 300],
                "filter0_count": [280, 275],
                "filter0_name": ["stock_pool_member", "stock_pool_member"],
                "filtered_count": [280, 275],
                "retention_rate": [280 / 300, 275 / 300],
            }).to_parquet(path)
        elif name == "group_returns":
            pd.DataFrame({
                "time": ["2020-01-01", "2020-01-02"],
                "factor_return_bottom": [0.01, 0.02],
                "factor_return_group0": [0.011, 0.021],
                "factor_return_group1": [0.012, 0.022],
                "factor_return_group2": [0.013, 0.023],
                "factor_return_group3": [0.014, 0.024],
                "factor_return_group4": [0.015, 0.025],
                "factor_return_top": [0.02, 0.04],
            }).to_parquet(path)
        elif name == "group_turnover":
            pd.DataFrame({
                "time": ["2020-01-01", "2020-01-02"],
                "factor": ["factor", "factor"],
                "periods": [1, 1],
                "rank_autocorrelation": [None, 0.8],
                "bottom": [None, 0.4],
                "group0": [None, 0.3],
                "group1": [None, 0.2],
                "group2": [None, 0.1],
                "group3": [None, 0.2],
                "group4": [None, 0.3],
                "top": [None, 0.5],
            }).to_parquet(path)
        elif name == "daily_portfolios":
            pd.DataFrame({"tradeDate": ["2020-01-01", "2020-01-02"], "ratio": [0.0, 0.01]}).to_parquet(path)
        else:
            path.write_bytes(b"result")
