from core.apps.backtest.services import PROJECT_OUTPUTS as BACKTEST_PROJECT_OUTPUTS
from core.apps.factor.services import PROJECT_OUTPUTS as FACTOR_PROJECT_OUTPUTS
from core.apps.query.services import PROJECT_OUTPUTS as QUERY_PROJECT_OUTPUTS
from core.scheduler.applications import DEFAULT_OUTPUT


def test_project_workflows_only_request_consumed_outputs() -> None:
    assert QUERY_PROJECT_OUTPUTS == ["data"]
    assert FACTOR_PROJECT_OUTPUTS == ["information_coefficient", "group_returns"]
    assert BACKTEST_PROJECT_OUTPUTS == [
        "trade_details",
        "daily_positions",
        "daily_portfolios",
        "daily_trading_statistics",
    ]


def test_workflow_definition_defaults_are_project_outputs() -> None:
    assert DEFAULT_OUTPUT == {
        "query": "data",
        "factor": "information_coefficient",
        "backtest": "daily_portfolios",
    }
