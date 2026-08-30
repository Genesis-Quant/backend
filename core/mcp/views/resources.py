"""MCP document, Runtime schema, and DSL catalog resources."""

from typing import Any

from mcp.server import MCPServer
from core.utils.dsl import DslCatalog, dsl_catalog
from core.utils.dsl_source import (
    BacktestApplicationRequest,
    FactorAnalysisApplicationRequest,
    QueryApplicationRequest,
)

from ..services import authenticated_document, document, runtime_schema


def register_resources(server: MCPServer) -> None:
    """Register every Arena MCP resource."""

    @server.resource(
        "arena://docs/overview/overview",
        title="Arena MCP 总览",
        description="当前用户提示词与删除权限、认证、文档导航、上游参考、源码核对和通用执行流程。",
        mime_type="text/markdown",
    )
    def overview_document() -> str:
        return authenticated_document("overview/overview")

    @server.resource(
        "arena://docs/overview/projects",
        title="Arena 对象关系",
        description="Project、Version、Workspace、Attempt、Workflow Instance 与 Task 的关系和保留边界。",
        mime_type="text/markdown",
    )
    def projects_document() -> str:
        return document("overview/projects")

    @server.resource(
        "arena://docs/overview/dsl",
        title="Factor Query DSL",
        description="JSON/Python 双源码保存、Runtime JSON 生成、派生节点、托管依赖、过滤器和算符发现方法。",
        mime_type="text/markdown",
    )
    def dsl_document() -> str:
        return document("overview/dsl")

    @server.resource(
        "arena://docs/overview/dolphindb",
        title="DolphinDB 脚本执行",
        description="认证用户只读 DolphinScript 测试接口、返回序列化、权限与计算资源边界。",
        mime_type="text/markdown",
    )
    def dolphindb_document() -> str:
        return document("overview/dolphindb")

    @server.resource(
        "arena://docs/overview/workflows",
        title="工作流与任务读取",
        description="Workspace 状态、Attempt 历史、Workflow、Task、日志分页、输出下载和控制。",
        mime_type="text/markdown",
    )
    def workflows_document() -> str:
        return document("overview/workflows")

    @server.resource(
        "arena://docs/query/request",
        title="Query 请求构造",
        description="FactorQuery 字段、执行顺序、代码范围、Schema 发现和提交前检查。",
        mime_type="text/markdown",
    )
    def query_request_document() -> str:
        return document("query/request")

    @server.resource(
        "arena://docs/query/api",
        title="Query API",
        description="Query 项目、运行、历史参数、输出与网页入口。",
        mime_type="text/markdown",
    )
    def query_api_document() -> str:
        return document("query/api")

    @server.resource(
        "arena://docs/factor/request",
        title="Factor 请求构造",
        description="两阶段查询、托管 stock_pool_member、因子列、收益列、预处理和提交前检查。",
        mime_type="text/markdown",
    )
    def factor_request_document() -> str:
        return document("factor/request")

    @server.resource(
        "arena://docs/factor/api",
        title="Factor API",
        description="Factor 项目、运行、版本、批量执行、输出与网页入口。",
        mime_type="text/markdown",
    )
    def factor_api_document() -> str:
        return document("factor/api")

    @server.resource(
        "arena://docs/backtest/request",
        title="Backtest 请求构造",
        description="查询、配置、参数、utils、八个回调、输出要求和提交前检查。",
        mime_type="text/markdown",
    )
    def backtest_request_document() -> str:
        return document("backtest/request")

    @server.resource(
        "arena://docs/backtest/api",
        title="Backtest API",
        description="Backtest 项目、运行、版本、批量执行、专项研究、输出与网页入口。",
        mime_type="text/markdown",
    )
    def backtest_api_document() -> str:
        return document("backtest/api")

    @server.resource(
        "arena://docs/backtest/dolphindb",
        title="DolphinDB Backtest 运行契约",
        description="价格尺度、合成快照、撮合、回调、共享因子预处理、持仓、资金和缺价行为。",
        mime_type="text/markdown",
    )
    def dolphindb_backtest_document() -> str:
        return document("backtest/dolphindb")

    @server.resource(
        "arena://docs/backtest/interfaces",
        title="Backtest 接口白名单",
        description="当前插件版本下策略可用、Runtime 独占和禁止接口的签名、返回形式、调用阶段与能力矩阵。",
        mime_type="text/markdown",
    )
    def backtest_interfaces_document() -> str:
        return document("backtest/interfaces")

    @server.resource(
        "arena://docs/backtest/results",
        title="Backtest 结果与审计契约",
        description="四张 Parquet 的完整字段、订单状态、成交字段标准化、费用、对账公式和结果 QA。",
        mime_type="text/markdown",
    )
    def backtest_results_document() -> str:
        return document("backtest/results")

    @server.resource(
        "arena://docs/backtest/dynamic-pool",
        title="动态数据域与时点契约",
        description="候选并集、逐日状态、退出保留和 point-in-time 边界。",
        mime_type="text/markdown",
    )
    def backtest_dynamic_pool_document() -> str:
        return document("backtest/dynamic-pool")

    @server.resource(
        "arena://docs/backtest/optimization",
        title="二次规划与目标权重契约",
        description="OSQP 接口、矩阵维度、求解状态、数值容差和两阶段调仓。",
        mime_type="text/markdown",
    )
    def backtest_optimization_document() -> str:
        return document("backtest/optimization")

    @server.resource(
        "arena://docs/backtest/callback-data",
        title="回调与交易对象契约",
        description="message、持仓、订单和成交对象的读取规则及拒单诊断边界。",
        mime_type="text/markdown",
    )
    def backtest_callback_data_document() -> str:
        return document("backtest/callback-data")

    @server.resource(
        "arena://docs/backtest/qa",
        title="回测端到端与结果 QA",
        description="运行生命周期、四表核验、账务公式、指标口径、撮合限制和保存顺序。",
        mime_type="text/markdown",
    )
    def backtest_qa_document() -> str:
        return document("backtest/qa")

    @server.resource(
        "arena://schemas/query",
        title="Query 请求 Schema",
        description="支持 JSON 或 Python DSL 源码的数据查询请求 Schema。",
        mime_type="application/json",
    )
    def query_schema() -> dict[str, Any]:
        return runtime_schema(QueryApplicationRequest)

    @server.resource(
        "arena://schemas/factor",
        title="Factor 请求 Schema",
        description="支持 JSON 或 Python DSL 源码的因子分析请求 Schema。",
        mime_type="application/json",
    )
    def factor_schema() -> dict[str, Any]:
        return runtime_schema(FactorAnalysisApplicationRequest)

    @server.resource(
        "arena://schemas/backtest",
        title="Backtest 请求 Schema",
        description="支持 JSON 或 Python DSL 源码的策略回测请求 Schema。",
        mime_type="application/json",
    )
    def backtest_schema() -> dict[str, Any]:
        return runtime_schema(BacktestApplicationRequest)

    @server.resource(
        "arena://dsl/catalog",
        title="完整 DSL Catalog",
        description="全部基础因子和算符的名称、类型、返回类型及字段 JSON Schema。",
        mime_type="application/json",
    )
    def complete_dsl_catalog() -> DslCatalog:
        return dsl_catalog()


__all__ = ["register_resources"]
