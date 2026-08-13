"""MCP document, Runtime schema, and DSL catalog resources."""

from typing import Any

from mcp.server import MCPServer
from runtime import BacktestParameters, FactorAnalysisParameters, FactorQuery

from core.utils.dsl import DslCatalog, dsl_catalog

from ..services import document, runtime_schema


def register_resources(server: MCPServer) -> None:
    """Register every Arena MCP resource."""

    @server.resource(
        "arena://docs/overview/overview",
        title="Arena MCP 总览",
        description="认证、返回值、文档导航、发现工具、项目入口和通用执行流程。",
        mime_type="text/markdown",
    )
    def overview_document() -> str:
        return document("overview/overview")

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
        description="基础字段、派生节点、依赖、过滤器、时序边界和算符发现方法。",
        mime_type="text/markdown",
    )
    def dsl_document() -> str:
        return document("overview/dsl")

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
        description="FactorQuery 字段、执行顺序、代码范围、完整示例和提交前检查。",
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
        description="两阶段股票池、因子列、收益列、预处理、完整示例和提交前检查。",
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
        description="查询、配置、参数、utils、八个回调、输出要求和完整请求外形。",
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
        description="价格尺度、合成快照、撮合、订单簿、回调事件、持仓、资金、代码映射和结果 QA。",
        mime_type="text/markdown",
    )
    def dolphindb_backtest_document() -> str:
        return document("backtest/dolphindb")

    @server.resource(
        "arena://schemas/query",
        title="FactorQuery JSON Schema",
        description="Runtime 当前实际使用的 FactorQuery JSON Schema。",
        mime_type="application/json",
    )
    def query_schema() -> dict[str, Any]:
        return runtime_schema(FactorQuery)

    @server.resource(
        "arena://schemas/factor",
        title="FactorAnalysisParameters JSON Schema",
        description="Runtime 当前实际使用的因子分析参数 JSON Schema。",
        mime_type="application/json",
    )
    def factor_schema() -> dict[str, Any]:
        return runtime_schema(FactorAnalysisParameters)

    @server.resource(
        "arena://schemas/backtest",
        title="BacktestParameters JSON Schema",
        description="Runtime 当前实际使用的策略回测参数 JSON Schema。",
        mime_type="application/json",
    )
    def backtest_schema() -> dict[str, Any]:
        return runtime_schema(BacktestParameters)

    @server.resource(
        "arena://dsl/catalog",
        title="完整 DSL Catalog",
        description="全部基础因子和算符的名称、类型、返回类型及字段 JSON Schema。",
        mime_type="application/json",
    )
    def complete_dsl_catalog() -> DslCatalog:
        return dsl_catalog()


__all__ = ["register_resources"]
