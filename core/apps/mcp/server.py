"""MCP tools and resources backed by Arena application services."""

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, BaseModel, Field
from runtime import BacktestParameters, FactorAnalysisParameters, FactorQuery
from runtime.apps.backtest.api import compile_backtest_scripts
from runtime.database import create_session as create_dolphindb_session

from config import MCPSettings
from core.apps.backtest.schemas import (
    BacktestProjectCreate,
    BacktestProjectItem,
    BacktestProjectListItem,
    BacktestVersionListItem,
    BacktestVersionResponse,
)
from core.apps.backtest.services import (
    backtest_result_files,
    create_backtest_project,
    create_backtest_version,
    get_backtest_project,
    get_backtest_version,
    list_backtest_projects,
    list_backtest_versions,
    submit_project_backtest,
)
from core.apps.factor.schemas import (
    FactorProjectCreate,
    FactorProjectItem,
    FactorProjectListItem,
    FactorVersionListItem,
    FactorVersionResponse,
)
from core.apps.factor.services import (
    create_factor_project,
    create_factor_version,
    factor_result_files,
    get_factor_project,
    get_factor_version,
    list_factor_projects,
    list_factor_versions,
    submit_project_analysis,
)
from core.apps.query.schemas import QueryProjectCreate, QueryProjectItem, QueryProjectListItem
from core.apps.query.services import create_query_project, get_query_project, list_query_projects, query_result_files, submit_project_query
from core.apps.schemas import ProjectPage, WorkflowSubmitted
from core.apps.tasks.schemas import TaskLogResponse
from core.apps.tasks.services import TaskGatewayService
from core.apps.users.models import User
from core.apps.users.services import decode_user_id
from core.apps.workflows.schemas import (
    WorkflowAction,
    WorkflowActionResponse,
    WorkflowInformation,
    WorkflowWorkspaceListResponse,
    WorkflowWorkspaceStatus,
)
from core.apps.workflows.services import WorkflowGatewayService, current_workflow_instance
from core.database.session import database_session_factory
from core.scheduler.errors import DolphinSchedulerError
from core.utils.dsl import DslCatalog, DslOperator, dsl_catalog
from core.utils.results import ResultFile

type ApplicationName = Literal["query", "factor", "backtest"]
type VersionedApplicationName = Literal["factor", "backtest"]
type DocumentName = Literal["overview", "query", "factor", "backtest", "dolphindb-backtest", "dsl"]
type ProjectListResult = ProjectPage[QueryProjectListItem] | ProjectPage[FactorProjectListItem] | ProjectPage[BacktestProjectListItem]
type ProjectResult = QueryProjectItem | FactorProjectItem | BacktestProjectItem
type VersionListResult = list[FactorVersionListItem | BacktestVersionListItem]
type VersionResult = FactorVersionResponse | BacktestVersionResponse

DOCUMENT_DIRECTORY = Path(__file__).parents[3] / "docs"
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
CONTROL = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)


class DslOperatorSummary(BaseModel):
    op: str
    type: Literal["DIRECT", "TS", "CS"]
    output_kind: Literal["BOOL", "NUMBER", "ANY"]
    description: str


class DslOperatorSearchResult(BaseModel):
    factors: list[str]
    operators: list[DslOperatorSummary]
    matched: int
    returned: int


class DolphinFunctionDefinition(BaseModel):
    name: str
    is_command: bool
    user_defined: bool
    min_parameter_count: int
    max_parameter_count: int
    syntax: str
    documentation_url: str


class DolphinFunctionDefinitions(BaseModel):
    requested: list[str]
    definitions: list[DolphinFunctionDefinition]
    missing: list[str]


class WorkflowOutputFile(ResultFile[str]):
    download_url: str


class WorkflowOutputs(BaseModel):
    application: ApplicationName
    workflow_instance_id: int
    outputs: list[WorkflowOutputFile]


class McpResult[T](BaseModel):
    """Stable envelope used by every Arena MCP tool."""

    result: T


class ArenaTokenVerifier(TokenVerifier):
    """Validate the JWT issued by Arena's existing login endpoint."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            user_id = decode_user_id(token)
        except HTTPException:
            return None
        return AccessToken(
            token=token,
            client_id=str(user_id),
            subject=str(user_id),
            scopes=["arena"],
            resource=MCPSettings.ENDPOINT_URL,
        )


mcp_server = MCPServer(
    name="arena-quant",
    title="Arena Quant",
    description="Submit and inspect Arena query, factor-analysis, and backtest workflows.",
    instructions=(
        "Before constructing a request, read the matching arena://docs resource or call read_arena_document. "
        "Before writing backtest callbacks, also read arena://docs/dolphindb-backtest. "
        "Inspect every DolphinDB built-in whose signature you are not certain about with "
        "describe_dolphindb_functions; its result comes from defs() on the deployed DolphinDB, not a curated list. "
        "Never invent DSL operator fields: call list_dsl_operators and describe_dsl_operator. "
        "Every tool's business result is at CallToolResult.structuredContent.result. "
        "Create or select a project before running a workflow. A run returns workspace_id and "
        "workflow_instance_id; poll get_workspace_status until SUCCESS or a failure state, then call "
        "list_workflow_outputs. Factor and backtest versions can only be saved from a successful current workflow."
    ),
    version="0.1.0",
    token_verifier=ArenaTokenVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(MCPSettings.PUBLIC_URL),
        resource_server_url=AnyHttpUrl(MCPSettings.ENDPOINT_URL),
        required_scopes=["arena"],
        service_documentation_url=AnyHttpUrl(f"{MCPSettings.PUBLIC_URL}/docs"),
    ),
)
mcp_app = mcp_server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=MCPSettings.allowed_hosts(),
        allowed_origins=MCPSettings.allowed_origins(),
    ),
)


def document(name: DocumentName) -> str:
    path = DOCUMENT_DIRECTORY / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"MCP 文档不存在：{name}")
    return path.read_text(encoding="utf-8")


def current_user(session: Any) -> User:
    token = get_access_token()
    if token is None or token.subject is None:
        raise PermissionError("MCP 请求缺少有效的 Arena Bearer Token")
    try:
        user_id = int(token.subject)
    except ValueError as error:
        raise PermissionError("Arena Bearer Token 不包含有效用户") from error
    user = session.get(User, user_id)
    if user is None:
        raise PermissionError("Arena 用户不存在")
    return user


def submitted_workspace(session: Any, workspace_id: int) -> WorkflowSubmitted:
    workflow = current_workflow_instance(session, workspace_id)
    if workflow is None:
        raise DolphinSchedulerError("DolphinScheduler 未创建 workflow instance")
    return WorkflowSubmitted(workspace_id=workspace_id, workflow_instance_id=workflow.workflow_instance_id)


@mcp_server.resource("arena://docs/overview", title="Arena MCP 使用流程", description="认证、工具选择、异步工作流和结果读取流程。", mime_type="text/markdown")
def overview_document() -> str:
    return document("overview")


@mcp_server.resource("arena://docs/query", title="Query 请求文档", description="FactorQuery 全字段、DSL 构造规则和查询示例。", mime_type="text/markdown")
def query_document() -> str:
    return document("query")


@mcp_server.resource("arena://docs/factor", title="Factor 分析文档", description="两阶段股票池、因子列、收益列、预处理和完整示例。", mime_type="text/markdown")
def factor_document() -> str:
    return document("factor")


@mcp_server.resource("arena://docs/backtest", title="Backtest 请求文档", description="数据查询、配置、策略参数、八个回调和完整示例。", mime_type="text/markdown")
def backtest_document() -> str:
    return document("backtest")


@mcp_server.resource(
    "arena://docs/dolphindb-backtest",
    title="DolphinDB Backtest 股票接口参考",
    description="Arena 日频适配边界、行情消息、回调事件、订单、持仓、资金、未成交单、配置和结果字段。",
    mime_type="text/markdown",
)
def dolphindb_backtest_document() -> str:
    return document("dolphindb-backtest")


@mcp_server.resource("arena://docs/dsl", title="Factor Query DSL 文档", description="派生节点、依赖、过滤器、算符查询方法和常见算符示例。", mime_type="text/markdown")
def dsl_document() -> str:
    return document("dsl")


@mcp_server.resource("arena://schemas/query", title="FactorQuery JSON Schema", description="runtime 当前实际使用的 FactorQuery JSON Schema。", mime_type="application/json")
def query_schema() -> dict[str, Any]:
    return FactorQuery.model_json_schema()


@mcp_server.resource("arena://schemas/factor", title="FactorAnalysisParameters JSON Schema", description="runtime 当前实际使用的因子分析参数 JSON Schema。", mime_type="application/json")
def factor_schema() -> dict[str, Any]:
    return FactorAnalysisParameters.model_json_schema()


@mcp_server.resource("arena://schemas/backtest", title="BacktestParameters JSON Schema", description="runtime 当前实际使用的策略回测参数 JSON Schema。", mime_type="application/json")
def backtest_schema() -> dict[str, Any]:
    return BacktestParameters.model_json_schema()


@mcp_server.resource("arena://dsl/catalog", title="完整 DSL Catalog", description="全部基础因子和算符的名称、类型、返回类型及字段 JSON Schema。", mime_type="application/json")
def complete_dsl_catalog() -> DslCatalog:
    return dsl_catalog()


@mcp_server.tool(title="读取 Arena 请求文档", annotations=READ_ONLY)
def read_arena_document(name: Annotated[DocumentName, Field(description="overview、query、factor、backtest、dolphindb-backtest 或 dsl。")]) -> McpResult[str]:
    """Read the detailed construction guide before creating or running an Arena workflow."""
    return McpResult(result=document(name))


@mcp_server.tool(title="搜索 DSL 算符", annotations=READ_ONLY)
def list_dsl_operators(
    search: Annotated[str, Field(description="按算符名或描述搜索；空字符串返回全部摘要。", max_length=128)] = "",
    operator_type: Annotated[Literal["DIRECT", "TS", "CS"] | None, Field(description="可选计算类别筛选。DIRECT 逐行、TS 按股票时序、CS 按日期截面。")] = None,
    limit: Annotated[int, Field(ge=1, le=200, description="最多返回的算符数量。")] = 50,
) -> McpResult[DslOperatorSearchResult]:
    """List DSL operator summaries. Call describe_dsl_operator before constructing an operator node."""
    query = search.strip().lower()
    operators = [operator for operator in dsl_catalog().operators if operator_type is None or operator.type == operator_type]
    if query:
        operators = [operator for operator in operators if query in operator.op.lower() or query in operator.description.lower()]
    return McpResult(result=DslOperatorSearchResult.model_validate({
        "factors": dsl_catalog().factors,
        "operators": [operator.model_dump(mode="json", exclude={"definition"}) for operator in operators[:limit]],
        "matched": len(operators),
        "returned": min(len(operators), limit),
    }))


@mcp_server.tool(title="查看 DSL 算符定义", annotations=READ_ONLY)
def describe_dsl_operator(operator: Annotated[str, Field(min_length=1, max_length=128, description="完整算符名，例如 unary.pct_change 或 binary.gt。")]) -> McpResult[DslOperator]:
    """Return one operator's exact type, output kind, description, fields and params JSON Schema."""
    match = next((item for item in dsl_catalog().operators if item.op == operator.strip()), None)
    if match is None:
        raise ValueError(f"不存在 DSL 算符：{operator}")
    return McpResult(result=match)


@mcp_server.tool(title="查询 DolphinDB 函数签名", annotations=READ_ONLY)
def describe_dolphindb_functions(
    names: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=100,
            description="要查询的 DolphinDB 内置函数名。返回当前部署版本 defs() 的参数数量、语法及官方文档地址。",
        ),
    ],
) -> McpResult[DolphinFunctionDefinitions]:
    """Inspect exact function signatures from the connected DolphinDB instead of relying on a hand-maintained subset."""
    requested = list(dict.fromkeys(name.strip() for name in names))
    if any(not name.isidentifier() for name in requested):
        raise ValueError("DolphinDB 函数名只能包含标识符字符")
    session = create_dolphindb_session()
    try:
        session.upload({"coreMcpFunctionNames": requested})
        table = session.run(
            "select name, isCommand, userDefined, minParamCount, maxParamCount, syntax "
            "from defs() where name in coreMcpFunctionNames"
        )
    finally:
        session.close()
    definitions = [
        DolphinFunctionDefinition(
            name=str(row["name"]),
            is_command=bool(row["isCommand"]),
            user_defined=bool(row["userDefined"]),
            min_parameter_count=int(row["minParamCount"]),
            max_parameter_count=int(row["maxParamCount"]),
            syntax=str(row["syntax"]),
            documentation_url=f"https://docs.dolphindb.com/zh/Functions/{str(row['name'])[0].lower()}/{row['name']}.html",
        )
        for row in table.to_dict("records")
    ]
    found = {definition.name for definition in definitions}
    return McpResult(result=DolphinFunctionDefinitions(
        requested=requested,
        definitions=definitions,
        missing=[name for name in requested if name not in found],
    ))


@mcp_server.tool(title="列出项目", annotations=READ_ONLY)
def list_projects(
    application: Annotated[ApplicationName, Field(description="项目类型：query、factor 或 backtest。")],
    page: Annotated[int, Field(ge=1, description="页码，从 1 开始。")] = 1,
    page_size: Annotated[int, Field(ge=1, le=100, description="每页数量。")] = 20,
) -> McpResult[ProjectListResult]:
    """List projects owned by the authenticated Arena user."""
    with database_session_factory()() as session:
        user = current_user(session)
        if application == "query":
            return McpResult(result=ProjectPage[QueryProjectListItem].model_validate(list_query_projects(session, user.id, page, page_size)))
        if application == "factor":
            return McpResult(result=ProjectPage[FactorProjectListItem].model_validate(list_factor_projects(session, user.id, page, page_size)))
        return McpResult(result=ProjectPage[BacktestProjectListItem].model_validate(list_backtest_projects(session, user.id, page, page_size)))


@mcp_server.tool(title="创建项目", annotations=WRITE)
def create_project(
    application: Annotated[ApplicationName, Field(description="项目类型：query、factor 或 backtest。")],
    title: Annotated[str, Field(min_length=1, max_length=128, description="项目标题；会去除首尾空格。")],
) -> McpResult[ProjectResult]:
    """Create an Arena project. `structuredContent.result.id` is the project_id. Query projects are limited to five per user."""
    with database_session_factory()() as session:
        user = current_user(session)
        if application == "query":
            validated_title = QueryProjectCreate(title=title).title
            return McpResult(result=QueryProjectItem.model_validate(create_query_project(session, user.id, validated_title)))
        if application == "factor":
            validated_title = FactorProjectCreate(title=title).title
            return McpResult(result=FactorProjectItem.model_validate(create_factor_project(session, user.id, validated_title)))
        validated_title = BacktestProjectCreate(title=title).title
        return McpResult(result=BacktestProjectItem.model_validate(create_backtest_project(session, user.id, validated_title)))


@mcp_server.tool(title="获取项目", annotations=READ_ONLY)
def get_project(
    application: Annotated[ApplicationName, Field(description="项目类型：query、factor 或 backtest。")],
    project_id: Annotated[int, Field(gt=0, description="项目 ID。")],
) -> McpResult[ProjectResult]:
    """Get one owned project including its current draft or workflow summary."""
    with database_session_factory()() as session:
        user = current_user(session)
        if application == "query":
            return McpResult(result=QueryProjectItem.model_validate(get_query_project(session, user.id, project_id)))
        if application == "factor":
            return McpResult(result=FactorProjectItem.model_validate(get_factor_project(session, user.id, project_id)))
        return McpResult(result=BacktestProjectItem.model_validate(get_backtest_project(session, user.id, project_id)))


@mcp_server.tool(title="执行数据查询", annotations=WRITE)
def run_query(
    project_id: Annotated[int, Field(gt=0, description="已存在的 Query 项目 ID。")],
    request: Annotated[FactorQuery, Field(description="完整 FactorQuery。调用前阅读 arena://docs/query 和 arena://docs/dsl。")],
) -> McpResult[WorkflowSubmitted]:
    """Validate and submit one Query workflow. Returns IDs immediately; poll get_workspace_status."""
    with database_session_factory()() as session:
        user = current_user(session)
        workspace = submit_project_query(session, user.id, project_id, {"dataset_query": request.model_dump(mode="json")})
        return McpResult(result=submitted_workspace(session, workspace.id))


@mcp_server.tool(title="执行因子分析", annotations=WRITE)
def run_factor_analysis(
    project_id: Annotated[int, Field(gt=0, description="已存在的 Factor 项目 ID。")],
    parameters: Annotated[FactorAnalysisParameters, Field(description="完整因子分析参数。调用前阅读 arena://docs/factor 和 arena://docs/dsl。")],
) -> McpResult[WorkflowSubmitted]:
    """Validate and submit one Factor workflow. Supports optional first-stage dynamic stock-pool selection."""
    with database_session_factory()() as session:
        user = current_user(session)
        workspace = submit_project_analysis(session, user.id, project_id, parameters.model_dump(mode="json"))
        return McpResult(result=submitted_workspace(session, workspace.id))


@mcp_server.tool(title="执行策略回测", annotations=WRITE)
def run_backtest(
    project_id: Annotated[int, Field(gt=0, description="已存在的 Backtest 项目 ID。")],
    parameters: Annotated[BacktestParameters, Field(description="完整回测参数。调用前阅读 arena://docs/backtest 和 arena://docs/dsl。")],
) -> McpResult[WorkflowSubmitted]:
    """Compile scripts in DolphinDB, then submit one Backtest workflow. All eight fixed-name callbacks are required."""
    compile_backtest_scripts(parameters)
    with database_session_factory()() as session:
        user = current_user(session)
        workspace = submit_project_backtest(session, user.id, project_id, parameters.model_dump(mode="json"))
        return McpResult(result=submitted_workspace(session, workspace.id))


@mcp_server.tool(title="列出研究版本", annotations=READ_ONLY)
def list_versions(
    application: Annotated[VersionedApplicationName, Field(description="版本类型：factor 或 backtest。")],
    project_id: Annotated[int, Field(gt=0, description="项目 ID。")],
) -> McpResult[VersionListResult]:
    """List saved and current draft versions for a factor or backtest project."""
    with database_session_factory()() as session:
        user = current_user(session)
        if application == "factor":
            return McpResult(result=[FactorVersionListItem.model_validate(item) for item in list_factor_versions(session, user.id, project_id)])
        return McpResult(result=[BacktestVersionListItem.model_validate(item) for item in list_backtest_versions(session, user.id, project_id)])


@mcp_server.tool(title="获取研究版本", annotations=READ_ONLY)
def get_version(
    application: Annotated[VersionedApplicationName, Field(description="版本类型：factor 或 backtest。")],
    project_id: Annotated[int, Field(gt=0, description="项目 ID。")],
    version: Annotated[int, Field(gt=0, description="项目内版本号。")],
) -> McpResult[VersionResult]:
    """Get parameters, workflow binding and saved metrics for one factor or backtest version."""
    with database_session_factory()() as session:
        user = current_user(session)
        if application == "factor":
            return McpResult(result=FactorVersionResponse.model_validate(get_factor_version(session, user.id, project_id, version)))
        return McpResult(result=BacktestVersionResponse.model_validate(get_backtest_version(session, user.id, project_id, version)))


@mcp_server.tool(title="保存研究版本", annotations=WRITE)
def save_version(
    application: Annotated[VersionedApplicationName, Field(description="版本类型：factor 或 backtest。")],
    project_id: Annotated[int, Field(gt=0, description="项目 ID。")],
    workflow_instance_id: Annotated[int, Field(gt=0, description="当前成功工作流的 workflow_instance_id。")],
    remark: Annotated[str, Field(max_length=512, description="版本备注。")] = "",
) -> McpResult[VersionResult]:
    """Save the current successful factor or backtest workflow as an immutable version and calculate summary metrics."""
    with database_session_factory()() as session:
        user = current_user(session)
        if application == "factor":
            result = create_factor_version(session, user.id, project_id, workflow_instance_id, remark.strip())
            return McpResult(result=FactorVersionResponse.model_validate(result))
        result = create_backtest_version(session, user.id, project_id, workflow_instance_id, remark.strip())
        return McpResult(result=BacktestVersionResponse.model_validate(result))


@mcp_server.tool(title="列出工作流", annotations=READ_ONLY)
def list_workflows(
    application: Annotated[Literal["query", "factor", "backtest", "incremental"] | None, Field(description="可选应用筛选。")] = None,
    state: Annotated[Literal["active", "success", "failure"] | None, Field(description="可选状态分组筛选。")] = None,
    page: Annotated[int, Field(ge=1, description="页码，从 1 开始。")] = 1,
    page_size: Annotated[int, Field(ge=1, le=100, description="每页数量。")] = 20,
) -> McpResult[WorkflowWorkspaceListResponse]:
    """List authenticated user's workflow workspaces and current attempts."""
    with database_session_factory()() as session:
        user = current_user(session)
        result = WorkflowGatewayService().list(session, user, page, page_size, application, state)
        return McpResult(result=WorkflowWorkspaceListResponse.model_validate(result))


@mcp_server.tool(title="获取工作空间状态", annotations=READ_ONLY)
def get_workspace_status(workspace_id: Annotated[int, Field(gt=0, description="run 工具返回的 workspace_id。")]) -> McpResult[WorkflowWorkspaceStatus]:
    """Poll the current attempt state. Prefer this tool while submission, execution, or auto-save is in progress."""
    with database_session_factory()() as session:
        user = current_user(session)
        result = WorkflowGatewayService().workspace_status(session, user, workspace_id)
        return McpResult(result=WorkflowWorkspaceStatus.model_validate(result))


@mcp_server.tool(title="获取工作流详情", annotations=READ_ONLY)
def get_workflow_details(workflow_instance_id: Annotated[int, Field(gt=0, description="DolphinScheduler workflow instance ID。")]) -> McpResult[WorkflowInformation]:
    """Get scheduler definition, timing, request payload, requested outputs, tasks and events for one workflow instance."""
    with database_session_factory()() as session:
        user = current_user(session)
        result = WorkflowGatewayService().detail(session, user, workflow_instance_id)
        return McpResult(result=WorkflowInformation.model_validate(result))


@mcp_server.tool(title="分页读取任务日志", annotations=READ_ONLY)
def get_task_logs(
    workflow_instance_id: Annotated[int, Field(gt=0, description="工作流详情返回的 workflow instance ID。")],
    task_instance_id: Annotated[int, Field(gt=0, description="工作流详情中某个 Task 的 instance ID。")],
    skip_line_num: Annotated[int, Field(ge=0, description="从第几行开始读取；首次使用 0，后续使用上次返回的 next_line_num。")] = 0,
    limit: Annotated[int, Field(ge=1, le=10000, description="本页最多返回的日志行数。")] = 1000,
) -> McpResult[TaskLogResponse]:
    """Read one authenticated DolphinScheduler task log page for failure diagnosis or progress inspection."""
    with database_session_factory()() as session:
        user = current_user(session)
        result = TaskGatewayService().log(session, user, workflow_instance_id, task_instance_id, skip_line_num, limit)
        return McpResult(result=TaskLogResponse.model_validate(result))


@mcp_server.tool(title="列出工作流输出", annotations=READ_ONLY)
def list_workflow_outputs(
    application: Annotated[ApplicationName, Field(description="工作流应用：query、factor 或 backtest。")],
    workflow_instance_id: Annotated[int, Field(gt=0, description="成功且仍为当前 Attempt 的 workflow instance ID。")],
) -> McpResult[WorkflowOutputs]:
    """List generated Parquet outputs after SUCCESS, including authenticated REST download URLs."""
    with database_session_factory()() as session:
        user = current_user(session)
        if application == "query":
            items = query_result_files(session, user.id, workflow_instance_id)
        elif application == "factor":
            items = factor_result_files(session, user.id, workflow_instance_id)
        else:
            items = backtest_result_files(session, user.id, workflow_instance_id)
        outputs = [
            WorkflowOutputFile(
                **ResultFile[str].model_validate(item).model_dump(),
                download_url=f"{MCPSettings.PUBLIC_URL}/api/v1/{application}/workflows/{workflow_instance_id}/outputs/{item['name']}",
            )
            for item in items
        ]
        return McpResult(result=WorkflowOutputs(application=application, workflow_instance_id=workflow_instance_id, outputs=outputs))


@mcp_server.tool(title="控制工作流", annotations=CONTROL)
def control_workflow(
    workflow_instance_id: Annotated[int, Field(gt=0, description="需要控制的 workflow instance ID。")],
    action: Annotated[WorkflowAction, Field(description="stop、pause、resume、rerun 或 retry-failed。重跑会产生新的 Attempt。")],
) -> McpResult[WorkflowActionResponse]:
    """Perform a scheduler control action. This mutates execution state and may create another attempt."""
    with database_session_factory()() as session:
        user = current_user(session)
        result = WorkflowGatewayService().control(session, user, workflow_instance_id, action)
        return McpResult(result=WorkflowActionResponse.model_validate(result))
