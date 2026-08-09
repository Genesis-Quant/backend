from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from runtime import BacktestParameters

from core.apps.backtest.models import BacktestResearch
from core.apps.backtest.services import serialize_batch_research

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


def test_batch_research_waits_for_metrics_and_reports_result_errors() -> None:
    research = BacktestResearch(
        id=1,
        version_id=1,
        analysis_type="sensitivity",
        description="",
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    pending = serialize_batch_research(
        research,
        1,
        1,
        [{"state": "SUCCESS", "metrics": None, "result_error": None, "error": None}],
    )
    failed = serialize_batch_research(
        research,
        1,
        1,
        [
            {"state": "SUCCESS", "metrics": {"totalReturn": 0.1}, "result_error": None, "error": None},
            {"state": "SUCCESS", "metrics": None, "result_error": "invalid parquet", "error": None},
        ],
        include_items=True,
    )

    assert (pending["state"], pending["completed_count"], pending["failed_count"]) == ("RESULT_PENDING", 0, 0)
    assert (failed["state"], failed["completed_count"], failed["failed_count"]) == ("PARTIAL_SUCCESS", 1, 1)
    assert failed["error"] == "invalid parquet"
