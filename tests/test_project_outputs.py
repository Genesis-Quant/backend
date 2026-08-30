from core.apps.backtest.services import PROJECT_OUTPUTS as BACKTEST_PROJECT_OUTPUTS
from core.apps.factor.services import PROJECT_OUTPUTS as FACTOR_PROJECT_OUTPUTS
from core.apps.query.services import PROJECT_OUTPUTS as QUERY_PROJECT_OUTPUTS


def test_project_workflows_only_request_consumed_outputs() -> None:
    assert QUERY_PROJECT_OUTPUTS == ["data"]
    assert FACTOR_PROJECT_OUTPUTS == ["information_coefficient", "group_returns", "diagnostics"]
    assert BACKTEST_PROJECT_OUTPUTS == [
        "trade_details",
        "daily_positions",
        "daily_portfolios",
        "daily_trading_statistics",
    ]
