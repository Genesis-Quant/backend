import pytest
from pydantic import ValidationError

from apps.backtest.schemas import BacktestTaskCreate


def backtest_payload():
    return {
        "dataset_query": {
            "start_date": "2025-01-01",
            "end_date": "2025-01-31",
            "codes": ["000001.SZ"],
            "factors": ["close"],
        },
        "callbacks": {"onBar": "def onBar(mutable context, message, indicator) {}"},
        "output": ["return_summary"],
    }


def test_backtest_request_accepts_fixed_callback_names():
    request = BacktestTaskCreate.model_validate(backtest_payload())
    assert set(request.callbacks) == {"onBar"}


def test_backtest_request_rejects_unknown_callback_name():
    payload = backtest_payload()
    payload["callbacks"] = {"run": "def run() {}"}
    with pytest.raises(ValidationError, match="固定函数名"):
        BacktestTaskCreate.model_validate(payload)
