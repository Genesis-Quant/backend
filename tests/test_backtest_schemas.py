from copy import deepcopy

import pytest
from pydantic import ValidationError
from runtime import BacktestParameters

from core.apps.backtest.schemas import BacktestWorkflowCreate


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
        "output": ["daily_portfolios"],
    }


def test_workflow_api_uses_runtime_backtest_parameters() -> None:
    assert issubclass(BacktestWorkflowCreate, BacktestParameters)


def test_workflow_api_rejects_static_empty_stock_pool() -> None:
    payload = workflow_payload()
    payload["dataset_query"]["codes"] = []

    with pytest.raises(ValidationError, match="dataset_query.codes 不能为空"):
        BacktestWorkflowCreate.model_validate(payload)


def test_workflow_api_rejects_missing_callback() -> None:
    payload = workflow_payload()
    del payload["callbacks"]["finalize"]

    with pytest.raises(ValidationError, match="callbacks 缺少固定函数"):
        BacktestWorkflowCreate.model_validate(payload)
