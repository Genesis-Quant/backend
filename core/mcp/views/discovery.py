"""MCP documentation and schema discovery tools."""

from typing import Annotated, Literal

from mcp.server import MCPServer
from pydantic import Field

from runtime.database import create_session as create_dolphindb_session

from core.utils.dsl import DslOperator, dsl_catalog

from ..schemas import (
    DocumentName,
    DolphinFunctionDefinition,
    DolphinFunctionDefinitions,
    DslOperatorSearchResult,
    McpResult,
    READ_ONLY,
)
from ..services import document, optional_parameter_count


def register_discovery_tools(server: MCPServer) -> None:
    """Register documentation, DSL, and DolphinDB discovery tools."""

    @server.tool(title="读取 Arena 请求文档", annotations=READ_ONLY)
    def read_arena_document(name: Annotated[DocumentName, Field(description="文档名；可选值与 arena://docs/* Resources 一致。")]) -> McpResult[str]:
        """Read one Arena request document."""
        return McpResult(result=document(name))

    @server.tool(title="搜索 DSL 算符", annotations=READ_ONLY)
    def list_dsl_operators(
        search: Annotated[str, Field(description="按算符名或描述搜索；空字符串返回全部摘要。", max_length=128)] = "",
        operator_type: Annotated[Literal["DIRECT", "TS", "CS"] | None, Field(description="可选计算类别筛选。")] = None,
        limit: Annotated[int, Field(ge=1, le=200, description="最多返回的算符数量。")] = 50,
    ) -> McpResult[DslOperatorSearchResult]:
        """List DSL summaries; inspect a concrete operator before using it."""
        query = search.strip().lower()
        operators = [item for item in dsl_catalog().operators if operator_type is None or item.type == operator_type]
        if query:
            operators = [item for item in operators if query in item.op.lower() or query in item.description.lower()]
        return McpResult(result=DslOperatorSearchResult.model_validate({
            "factors": dsl_catalog().factors,
            "operators": [item.model_dump(mode="json", exclude={"definition"}) for item in operators[:limit]],
            "matched": len(operators),
            "returned": min(len(operators), limit),
        }))

    @server.tool(title="查看 DSL 算符定义", annotations=READ_ONLY)
    def describe_dsl_operator(operator: Annotated[str, Field(min_length=1, max_length=128, description="完整算符名，例如 unary.pct_change 或 binary.gt。")]) -> McpResult[DslOperator]:
        """Return one operator's exact fields and params schema."""
        match = next((item for item in dsl_catalog().operators if item.op == operator.strip()), None)
        if match is None:
            raise ValueError(f"不存在 DSL 算符：{operator}")
        return McpResult(result=match)

    @server.tool(title="查询 DolphinDB 函数签名", annotations=READ_ONLY)
    def describe_dolphindb_functions(
        names: Annotated[list[str], Field(min_length=1, max_length=100, description="DolphinDB defs() 内置函数简单名称；命名空间 helper 不在此查询。")],
    ) -> McpResult[DolphinFunctionDefinitions]:
        """Inspect exact function signatures from the connected DolphinDB."""
        requested = list(dict.fromkeys(name.strip() for name in names))
        valid_names = [name for name in requested if name.isidentifier()]
        if valid_names:
            session = create_dolphindb_session()
            try:
                session.upload({"coreMcpFunctionNames": valid_names})
                table = session.run(
                    "select name, isCommand, userDefined, minParamCount, maxParamCount, syntax "
                    "from defs() where name in coreMcpFunctionNames"
                )
            finally:
                session.close()
        else:
            table = []
        records = table.to_dict("records") if hasattr(table, "to_dict") else []
        definitions = [
            DolphinFunctionDefinition(
                name=str(row["name"]),
                is_command=bool(row["isCommand"]),
                user_defined=bool(row["userDefined"]),
                min_parameter_count=optional_parameter_count(row["minParamCount"]),
                max_parameter_count=optional_parameter_count(row["maxParamCount"]),
                syntax=str(row["syntax"]),
                documentation_url=f"https://docs.dolphindb.com/zh/Functions/{str(row['name'])[0].lower()}/{row['name']}.html",
            )
            for row in records
        ]
        found = {item.name for item in definitions}
        return McpResult(result=DolphinFunctionDefinitions(
            requested=requested,
            definitions=definitions,
            missing=[name for name in requested if name not in found],
        ))


__all__ = ["register_discovery_tools"]
