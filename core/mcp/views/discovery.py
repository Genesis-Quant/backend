"""MCP documentation and schema discovery tools."""

from typing import Annotated, Literal

from mcp.server import MCPServer
from pydantic import Field

from runtime.database import create_session as create_dolphindb_session

from core.utils.dsl import DslOperator, PythonDslCompileError, dsl_catalog
from core.utils.dsl_source import (
    PYTHON_DSL_SOURCE_MAX_LENGTH,
    DslDocument,
    DslSource,
    compile_backtest_dsl_source,
    compile_dsl_source,
    compile_factor_dsl_source,
)

from ..schemas import (
    DocumentName,
    DolphinFunctionDefinition,
    DolphinFunctionDefinitions,
    DslCompilationApplication,
    DslOperatorSearchResult,
    McpResult,
    PythonDslCompilationResult,
    READ_ONLY,
)
from ..services import authenticated_document, optional_parameter_count


def register_discovery_tools(server: MCPServer) -> None:
    """Register documentation, DSL, and DolphinDB discovery tools."""

    @server.tool(title="读取 Arena 请求文档", annotations=READ_ONLY)
    def read_arena_document(
        name: Annotated[DocumentName, Field(description="文档名；可选值与 arena://docs/* Resources 一致。")],
    ) -> McpResult[str]:
        """Read one Arena request document."""
        return McpResult(result=authenticated_document(name))

    @server.tool(title="搜索 DSL 算符", annotations=READ_ONLY)
    def list_dsl_operators(
        search: Annotated[str, Field(description="只按算符名或描述搜索；不筛选返回的完整 factors 列表。", max_length=128)] = "",
        operator_type: Annotated[Literal["DIRECT", "TS", "CS"] | None, Field(description="可选计算类别筛选。")] = None,
        limit: Annotated[int, Field(ge=1, le=200, description="最多返回的算符数量。")] = 50,
    ) -> McpResult[DslOperatorSearchResult]:
        """List operator summaries and the complete Runtime factor allowlist."""
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

    @server.tool(title="编译 Python DSL", annotations=READ_ONLY)
    def compile_python_dsl(
        python_source: Annotated[
            str,
            Field(
                min_length=1,
                max_length=PYTHON_DSL_SOURCE_MAX_LENGTH,
                description=(
                    "完整 Python Factor Query DSL 源码；必须定义 FACTORS 和 "
                    "FILTERS。只做编译与校验，不创建项目或工作流。"
                ),
            ),
        ],
        application: Annotated[
            DslCompilationApplication,
            Field(
                description=(
                    "编译上下文：run_query 及所有 codes_query 使用 query；"
                    "Factor/Backtest 的 dataset_query 分别使用 factor/backtest，"
                    "以允许引用 Backend 托管的 stock_pool_member。"
                ),
            ),
        ] = "query",
    ) -> McpResult[PythonDslCompilationResult]:
        """Compile Python DSL into the exact JSON document used by Arena."""
        source = DslSource(
            language="python",
            json_source="",
            python_source=python_source,
        )
        compiler = {
            "query": compile_dsl_source,
            "factor": compile_factor_dsl_source,
            "backtest": compile_backtest_dsl_source,
        }[application]
        try:
            compiled = DslDocument.model_validate(compiler(source))
        except (PythonDslCompileError, ValueError) as error:
            return McpResult(result=PythonDslCompilationResult(
                application=application,
                success=False,
                error_reason=str(error),
            ))
        return McpResult(result=PythonDslCompilationResult(
            application=application,
            success=True,
            compiled_json=compiled,
        ))

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
