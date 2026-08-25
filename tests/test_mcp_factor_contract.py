"""MCP 因子请求的网页兼容股票池契约。"""

import pytest
from runtime import FactorAnalysisParameters

from core.mcp.contracts import validate_mcp_factor_parameters


def factor_parameters(
    codes_factor: str = "weight_000852SH",
    dataset_factor: str = "weight_000852SH",
    alias: bool = False,
) -> FactorAnalysisParameters:
    def member(factor: str) -> dict[str, object]:
        return {
            "type": "DIRECT",
            "op": "binary.gt",
            "fields": {"left": factor, "right": 0},
            "params": {},
        }

    dataset_derivatives = {
        "stock_pool_member": member(dataset_factor),
        "future_return": {
            "type": "TS",
            "op": "unary.pct_change",
            "fields": {"col": "close"},
            "params": {"periods": 1},
        },
    }
    filters = ["stock_pool_member"]
    if alias:
        dataset_derivatives["is_member"] = member(dataset_factor)
        filters.append("is_member")
    return FactorAnalysisParameters.model_validate({
        "codes_query": {
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
            "lookback": "P0D",
            "codes": [],
            "factors": [],
            "derivatives": {"stock_pool_member": member(codes_factor)},
            "filters": ["stock_pool_member"],
        },
        "dataset_query": {
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
            "lookback": "P30D",
            "codes": [],
            "factors": ["close"],
            "derivatives": dataset_derivatives,
            "filters": filters,
        },
        "factor_columns": ["close"],
        "return_columns": ["future_return"],
        "return_specs": {
            "future_return": {"kind": "simple", "periods": 1},
        },
    })


def test_mcp_factor_contract_accepts_matching_managed_stock_pool() -> None:
    parameters = factor_parameters()

    assert validate_mcp_factor_parameters(parameters) is parameters


def test_mcp_factor_contract_rejects_different_stage_pools() -> None:
    with pytest.raises(ValueError, match="必须使用同一股票池"):
        validate_mcp_factor_parameters(
            factor_parameters(codes_factor="weight_000300SH")
        )


def test_mcp_factor_contract_rejects_membership_alias() -> None:
    with pytest.raises(ValueError, match="只能使用 stock_pool_member"):
        validate_mcp_factor_parameters(factor_parameters(alias=True))


def test_mcp_factor_contract_requires_codes_query() -> None:
    parameters = factor_parameters().model_copy(update={"codes_query": None})

    with pytest.raises(ValueError, match="必须提供 codes_query"):
        validate_mcp_factor_parameters(parameters)
