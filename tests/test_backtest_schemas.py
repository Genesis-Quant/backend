from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from runtime import BacktestParameters

from core.apps.backtest.models import BacktestResearch
from core.apps.backtest.services import serialize_batch_research
from core.apps.workflows.models import (
    WorkflowAttempt,
    WorkflowInstance,
    WorkflowWorkspace,
)

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


def workflow_payload() -> dict:
    return {
        "codes_query": None,
        "dataset_query": {
            "start_date": "2020-01-01",
            "end_date": "2020-01-31",
            "codes": ["000001.SZ"],
            "factors": ["close"],
        },
        "callbacks": deepcopy(CALLBACKS),
    }


@pytest.mark.parametrize("name", ["name", "source_ref", "message_ref"])
def test_project_run_rejects_runtime_only_parameters(name: str) -> None:
    payload = workflow_payload()
    payload[name] = "customValue"

    with pytest.raises(ValidationError):
        BacktestParameters.model_validate(payload)


def test_workflow_api_rejects_static_empty_stock_pool() -> None:
    payload = workflow_payload()
    payload["dataset_query"]["codes"] = []

    with pytest.raises(ValidationError, match="dataset_query.codes 不能为空"):
        BacktestParameters.model_validate(payload)


def test_workflow_api_rejects_missing_callback() -> None:
    payload = workflow_payload()
    del payload["callbacks"]["finalize"]

    with pytest.raises(ValidationError, match="callbacks 缺少固定函数"):
        BacktestParameters.model_validate(payload)


def batch_research_objects() -> tuple[
    BacktestResearch,
    WorkflowWorkspace,
    WorkflowAttempt,
    WorkflowInstance,
]:
    timestamp = datetime(2026, 8, 21, tzinfo=UTC)
    research = BacktestResearch(
        id=1,
        version_id=1,
        workflow_workspace_id=2,
        analysis_type="sensitivity",
        description="",
        created_at=timestamp,
    )
    workspace = WorkflowWorkspace(
        id=2,
        user_id=3,
        application="sensitivity",
        created_at=timestamp,
    )
    attempt = WorkflowAttempt(
        id=4,
        workflow_workspace_id=workspace.id,
        is_current=True,
        submission_state="SUBMITTED",
        input_json={"cases": [{}, {}]},
        start_parameters={},
        requested_outputs=["results"],
        events=[],
        created_at=timestamp,
        updated_at=timestamp,
    )
    workflow = WorkflowInstance(
        workflow_instance_id=5,
        workflow_attempt_id=attempt.id,
        state="SUCCESS",
        state_history=[],
        created_at=timestamp,
        updated_at=timestamp,
    )
    return research, workspace, attempt, workflow


def test_batch_research_waits_for_current_result_summary() -> None:
    research, workspace, attempt, workflow = batch_research_objects()

    response = serialize_batch_research(
        research,
        10,
        1,
        workspace,
        attempt,
        workflow,
    )

    assert response["state"] == "RESULT_PENDING"
    assert response["completed_count"] == 0
    assert response["failed_count"] == 0


def test_batch_research_ignores_summary_from_superseded_instance() -> None:
    research, workspace, attempt, workflow = batch_research_objects()
    research.result_workflow_instance_id = workflow.workflow_instance_id - 1
    research.completed_count = 2
    research.failed_count = 0

    response = serialize_batch_research(
        research,
        10,
        1,
        workspace,
        attempt,
        workflow,
    )

    assert response["state"] == "RESULT_PENDING"
    assert response["completed_count"] == 0
    assert response["failed_count"] == 0
