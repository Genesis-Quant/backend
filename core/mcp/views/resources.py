"""MCP document, Runtime schema, and DSL catalog resources."""

from typing import Any

from mcp.server import MCPServer
from runtime import BacktestParameters, FactorAnalysisParameters, FactorQuery

from core.utils.dsl import DslCatalog, dsl_catalog

from ..services import document, runtime_schema


def register_resources(server: MCPServer) -> None:
    """Register every Arena MCP resource."""

    @server.resource("arena://docs/overview", title="Arena MCP 使用流程", description="认证、工具选择、异步工作流和结果读取流程。", mime_type="text/markdown")
    def overview_document() -> str:
        return document("overview")

    @server.resource("arena://docs/tools", title="Arena MCP 工具参考", description="全部 MCP 工具的参数、返回值、调用条件和错误处理。", mime_type="text/markdown")
    def tools_document() -> str:
        return document("tools")

    @server.resource("arena://docs/query", title="Query 请求文档", description="FactorQuery 全字段、DSL 构造规则和查询示例。", mime_type="text/markdown")
    def query_document() -> str:
        return document("query")

    @server.resource("arena://docs/factor", title="Factor 分析文档", description="两阶段股票池、因子列、收益列、预处理和完整示例。", mime_type="text/markdown")
    def factor_document() -> str:
        return document("factor")

    @server.resource("arena://docs/backtest", title="Backtest 请求文档", description="数据查询、配置、策略参数、八个回调和完整示例。", mime_type="text/markdown")
    def backtest_document() -> str:
        return document("backtest")

    @server.resource(
        "arena://docs/dolphindb-backtest",
        title="DolphinDB Backtest 股票接口参考",
        description="Arena 日频适配边界、行情消息、回调事件、订单、持仓、资金、未成交单、配置和结果字段。",
        mime_type="text/markdown",
    )
    def dolphindb_backtest_document() -> str:
        return document("dolphindb-backtest")

    @server.resource("arena://docs/dsl", title="Factor Query DSL 文档", description="派生节点、依赖、过滤器和算符查询方法。", mime_type="text/markdown")
    def dsl_document() -> str:
        return document("dsl")

    @server.resource("arena://schemas/query", title="FactorQuery JSON Schema", description="Runtime 当前实际使用的 FactorQuery JSON Schema。", mime_type="application/json")
    def query_schema() -> dict[str, Any]:
        return runtime_schema(FactorQuery)

    @server.resource("arena://schemas/factor", title="FactorAnalysisParameters JSON Schema", description="Runtime 当前实际使用的因子分析参数 JSON Schema。", mime_type="application/json")
    def factor_schema() -> dict[str, Any]:
        return runtime_schema(FactorAnalysisParameters)

    @server.resource("arena://schemas/backtest", title="BacktestParameters JSON Schema", description="Runtime 当前实际使用的策略回测参数 JSON Schema。", mime_type="application/json")
    def backtest_schema() -> dict[str, Any]:
        return runtime_schema(BacktestParameters)

    @server.resource("arena://dsl/catalog", title="完整 DSL Catalog", description="全部基础因子和算符的名称、类型、返回类型及字段 JSON Schema。", mime_type="application/json")
    def complete_dsl_catalog() -> DslCatalog:
        return dsl_catalog()


__all__ = ["register_resources"]
