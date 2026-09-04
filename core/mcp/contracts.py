"""MCP 与 Web 共用的 Factor 股票池请求契约。"""

from runtime import FactorAnalysisParameters

from core.utils.dsl_source import (
    FACTOR_ALL_MARKET_STOCK_POOL_NODE,
    FACTOR_STOCK_POOL_FACTORS,
)


def validate_mcp_factor_parameters(
    parameters: FactorAnalysisParameters,
) -> FactorAnalysisParameters:
    """要求 MCP 因子请求满足 Web 共用的股票池结构。"""
    if parameters.codes_query is None:
        if parameters.dataset_query.codes:
            raise ValueError(
                "MCP 因子分析使用 codes_query=null 时代表全市场，"
                "dataset_query.codes 必须为空列表"
            )
        member = parameters.dataset_query.derivatives.get(
            "stock_pool_member"
        )
        if (
            member is None
            or member.model_dump(mode="json")
            != FACTOR_ALL_MARKET_STOCK_POOL_NODE
        ):
            raise ValueError(
                "全市场因子分析的运行参数必须包含 Backend 托管的恒真 "
                "dataset_query.derivatives.stock_pool_member"
            )
        if "stock_pool_member" in parameters.dataset_query.filters:
            raise ValueError(
                "全市场因子分析不能使用 stock_pool_member 过滤数据"
            )
        return parameters

    selected_factors: dict[str, str] = {}
    for location, query in (
        ("codes_query", parameters.codes_query),
        ("dataset_query", parameters.dataset_query),
    ):
        member = query.derivatives.get("stock_pool_member")
        if member is None:
            raise ValueError(
                f"{location}.derivatives.stock_pool_member 为 MCP 因子分析必填项"
            )
        definition = member.model_dump(mode="json")
        fields = definition.get("fields")
        left = fields.get("left") if isinstance(fields, dict) else None
        right = fields.get("right") if isinstance(fields, dict) else None
        if (
            definition.get("type") != "DIRECT"
            or definition.get("op") != "binary.gt"
            or left not in FACTOR_STOCK_POOL_FACTORS
            or isinstance(right, bool)
            or not isinstance(right, (int, float))
            or right != 0
            or definition.get("params") != {}
        ):
            raise ValueError(
                f"{location}.derivatives.stock_pool_member 必须是受支持指数权重 "
                "大于 0 的 DIRECT binary.gt 节点"
            )
        if "stock_pool_member" not in query.filters:
            raise ValueError(
                f"{location}.filters 必须包含 stock_pool_member"
            )

        for name, derivative in query.derivatives.items():
            if name == "stock_pool_member":
                continue
            candidate = derivative.model_dump(mode="json")
            candidate_fields = candidate.get("fields")
            if not isinstance(candidate_fields, dict):
                continue
            candidate_right = candidate_fields.get("right")
            if (
                candidate.get("type") == "DIRECT"
                and candidate.get("op") == "binary.gt"
                and candidate_fields.get("left") in FACTOR_STOCK_POOL_FACTORS
                and not isinstance(candidate_right, bool)
                and isinstance(candidate_right, (int, float))
                and candidate_right == 0
            ):
                raise ValueError(
                    f"{location}.derivatives.{name} 重复定义了股票池成员条件；"
                    "MCP/Web 请求只能使用 stock_pool_member"
                )
        selected_factors[location] = left

    if selected_factors["codes_query"] != selected_factors["dataset_query"]:
        raise ValueError(
            "codes_query 与 dataset_query 的 stock_pool_member 必须使用同一股票池："
            f"{selected_factors['codes_query']} != "
            f"{selected_factors['dataset_query']}"
        )
    return parameters


__all__ = ["validate_mcp_factor_parameters"]
