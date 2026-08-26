"""MCP project, execution, and version tools."""

from typing import Annotated, Any, Literal, get_args

from mcp.server import MCPServer
from pydantic import Field
from runtime import BacktestParameters, FactorAnalysisParameters, FactorQuery
from runtime.apps.backtest.api import compile_backtest_scripts

from core.apps.backtest.schemas import (
    BacktestProjectSortField,
    BacktestProjectCreate,
    BacktestProjectItem,
    BacktestProjectListItem,
    BacktestProjectUpdate,
    BacktestVersionListItem,
    BacktestVersionResponse,
    BacktestVersionUpdate,
)
from core.apps.backtest.services import (
    create_backtest_project,
    create_backtest_version,
    get_backtest_project,
    get_backtest_version,
    list_backtest_projects,
    list_backtest_versions,
    submit_project_backtest,
    update_backtest_project,
    update_backtest_version,
)
from core.apps.factor.schemas import (
    FactorProjectSortField,
    FactorProjectCreate,
    FactorProjectItem,
    FactorProjectListItem,
    FactorProjectUpdate,
    FactorVersionListItem,
    FactorVersionResponse,
    FactorVersionUpdate,
)
from core.apps.factor.services import (
    create_factor_project,
    create_factor_version,
    get_factor_project,
    get_factor_version,
    list_factor_projects,
    list_factor_versions,
    submit_project_analysis,
    update_factor_project,
    update_factor_version,
)
from core.apps.query.schemas import QueryProjectCreate, QueryProjectItem, QueryProjectListItem, QueryProjectSortField
from core.apps.query.services import create_query_project, get_query_project, list_query_projects, submit_project_query
from core.apps.schemas import ProjectPage, SortOrder, WorkflowSubmitted
from core.database.session import database_session_factory

from ..auth import current_user
from ..contracts import validate_mcp_factor_parameters
from ..schemas import (
    McpResult,
    ProjectListResult,
    ProjectResult,
    READ_ONLY,
    VersionListResult,
    VersionedProjectResult,
    VersionResult,
    WRITE,
)
from ..services import submitted_workspace

type ProjectApplication = Literal["query", "factor", "backtest"]
type ProjectSortField = QueryProjectSortField | FactorProjectSortField | BacktestProjectSortField

PROJECT_SORT_FIELDS = {
    "query": frozenset(get_args(QueryProjectSortField.__value__)),
    "factor": frozenset(get_args(FactorProjectSortField.__value__)),
    "backtest": frozenset(get_args(BacktestProjectSortField.__value__)),
}


def validate_project_sort_field(
    application: ProjectApplication,
    sort_by: ProjectSortField,
) -> ProjectSortField:
    """Reject sort fields that belong to a different project application."""
    if sort_by not in PROJECT_SORT_FIELDS[application]:
        available = ", ".join(sorted(PROJECT_SORT_FIELDS[application]))
        raise ValueError(f"{application} 项目不支持按 {sort_by} 排序；可用字段：{available}")
    return sort_by


def register_project_tools(server: MCPServer) -> None:
    """Register project CRUD, run, and version tools."""

    @server.tool(title="列出项目", annotations=READ_ONLY)
    def list_projects(
        application: Annotated[ProjectApplication, Field(description="项目类型。")],
        page: Annotated[int, Field(ge=1, description="页码，从 1 开始。")] = 1,
        page_size: Annotated[int, Field(ge=1, le=100, description="每页数量。")] = 20,
        search: Annotated[str | None, Field(max_length=128, description="按项目名称或 ID 片段搜索；空值表示不过滤。")] = None,
        sort_by: Annotated[
            ProjectSortField,
            Field(description="排序字段；每种 application 支持的字段见对应 API 文档。"),
        ] = "updated_at",
        sort_order: Annotated[SortOrder, Field(description="升序 asc 或降序 desc。")] = "desc",
    ) -> McpResult[ProjectListResult]:
        """List projects owned by the authenticated user."""
        validated_sort_by = validate_project_sort_field(application, sort_by)
        with database_session_factory()() as session:
            user = current_user(session)
            if application == "query":
                result = ProjectPage[QueryProjectListItem].model_validate(
                    list_query_projects(session, user.id, page, page_size, search, validated_sort_by, sort_order)
                )
            elif application == "factor":
                result = ProjectPage[FactorProjectListItem].model_validate(
                    list_factor_projects(session, user.id, page, page_size, search, validated_sort_by, sort_order)
                )
            else:
                result = ProjectPage[BacktestProjectListItem].model_validate(
                    list_backtest_projects(session, user.id, page, page_size, search, validated_sort_by, sort_order)
                )
            return McpResult(result=result)

    @server.tool(title="创建项目", annotations=WRITE)
    def create_project(
        application: Annotated[Literal["query", "factor", "backtest"], Field(description="项目类型。")],
        title: Annotated[str, Field(min_length=1, max_length=128, description="项目标题；会去除首尾空格。")],
    ) -> McpResult[ProjectResult]:
        """Create an Arena project and return its project ID."""
        with database_session_factory()() as session:
            user = current_user(session)
            if application == "query":
                validated_title = QueryProjectCreate(title=title).title
                result = QueryProjectItem.model_validate(create_query_project(session, user.id, validated_title))
            elif application == "factor":
                validated_title = FactorProjectCreate(title=title).title
                result = FactorProjectItem.model_validate(create_factor_project(session, user.id, validated_title))
            else:
                validated_title = BacktestProjectCreate(title=title).title
                result = BacktestProjectItem.model_validate(create_backtest_project(session, user.id, validated_title))
            return McpResult(result=result)

    @server.tool(title="获取项目", annotations=READ_ONLY)
    def get_project(
        application: Annotated[Literal["query", "factor", "backtest"], Field(description="项目类型。")],
        project_id: Annotated[int, Field(gt=0, description="项目 ID。")],
    ) -> McpResult[ProjectResult]:
        """Get one project including its current workflow or draft."""
        with database_session_factory()() as session:
            user = current_user(session)
            if application == "query":
                result = QueryProjectItem.model_validate(get_query_project(session, user.id, project_id))
            elif application == "factor":
                result = FactorProjectItem.model_validate(get_factor_project(session, user.id, project_id))
            else:
                result = BacktestProjectItem.model_validate(get_backtest_project(session, user.id, project_id))
            return McpResult(result=result)

    @server.tool(title="重命名项目", annotations=WRITE)
    def update_project(
        application: Annotated[Literal["factor", "backtest"], Field(description="项目类型。Query 项目当前不支持重命名。")],
        project_id: Annotated[int, Field(gt=0, description="项目 ID。")],
        title: Annotated[str, Field(min_length=1, max_length=128, description="新标题。")],
    ) -> McpResult[VersionedProjectResult]:
        """Rename a Factor or Backtest project."""
        with database_session_factory()() as session:
            user = current_user(session)
            if application == "factor":
                validated = FactorProjectUpdate(title=title)
                result = FactorProjectItem.model_validate(update_factor_project(session, user.id, project_id, validated.title))
            else:
                validated = BacktestProjectUpdate(title=title)
                result = BacktestProjectItem.model_validate(update_backtest_project(session, user.id, project_id, validated.title))
            return McpResult(result=result)

    @server.tool(title="执行数据查询", annotations=WRITE)
    def run_query(
        project_id: Annotated[int, Field(gt=0, description="已存在的 Query 项目 ID。")],
        request: Annotated[
            dict[str, Any],
            Field(description="FactorQuery 对象；必填 start_date/end_date，lookback/codes/factors/derivatives/filters 有默认值，精确契约见 Query Schema。"),
        ],
    ) -> McpResult[WorkflowSubmitted]:
        """Validate and submit one Query workflow."""
        validated = FactorQuery.model_validate(request)
        with database_session_factory()() as session:
            user = current_user(session)
            workspace = submit_project_query(session, user.id, project_id, {"dataset_query": validated.model_dump(mode="json")})
            return McpResult(result=submitted_workspace(session, workspace.id))

    @server.tool(title="执行因子分析", annotations=WRITE)
    def run_factor_analysis(
        project_id: Annotated[int, Field(gt=0, description="已存在的 Factor 项目 ID。")],
        parameters: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "完整因子分析对象；全市场使用 codes_query=null、"
                    "dataset_query.codes=[] 且不定义 stock_pool_member；指数池则要求"
                    "两阶段使用同一受支持指数权重定义并过滤 stock_pool_member。"
                )
            ),
        ],
    ) -> McpResult[WorkflowSubmitted]:
        """Validate and submit one Factor workflow."""
        validated = validate_mcp_factor_parameters(
            FactorAnalysisParameters.model_validate(parameters)
        )
        with database_session_factory()() as session:
            user = current_user(session)
            workspace = submit_project_analysis(session, user.id, project_id, validated.model_dump(mode="json"))
            return McpResult(result=submitted_workspace(session, workspace.id))

    @server.tool(title="执行策略回测", annotations=WRITE)
    def run_backtest(
        project_id: Annotated[int, Field(gt=0, description="已存在的 Backtest 项目 ID。")],
        parameters: Annotated[
            dict[str, Any],
            Field(description="回测对象；必填 dataset_query/callbacks，config/params/codes_query/adj/年化参数/utils 有默认值，精确契约见 Backtest Schema。"),
        ],
    ) -> McpResult[WorkflowSubmitted]:
        """Compile scripts in DolphinDB and submit one Backtest workflow."""
        validated = BacktestParameters.model_validate(parameters)
        with database_session_factory()() as session:
            user = current_user(session)
            get_backtest_project(session, user.id, project_id)
        compile_backtest_scripts(validated)
        with database_session_factory()() as session:
            user = current_user(session)
            workspace = submit_project_backtest(session, user.id, project_id, validated.model_dump(mode="json"))
            return McpResult(result=submitted_workspace(session, workspace.id))

    @server.tool(title="列出研究版本", annotations=READ_ONLY)
    def list_versions(
        application: Annotated[Literal["factor", "backtest"], Field(description="版本类型。")],
        project_id: Annotated[int, Field(gt=0, description="项目 ID。")],
    ) -> McpResult[VersionListResult]:
        """List saved and current draft versions."""
        with database_session_factory()() as session:
            user = current_user(session)
            if application == "factor":
                result = [FactorVersionListItem.model_validate(item) for item in list_factor_versions(session, user.id, project_id)]
            else:
                result = [BacktestVersionListItem.model_validate(item) for item in list_backtest_versions(session, user.id, project_id)]
            return McpResult(result=result)

    @server.tool(title="获取研究版本", annotations=READ_ONLY)
    def get_version(
        application: Annotated[Literal["factor", "backtest"], Field(description="版本类型。")],
        project_id: Annotated[int, Field(gt=0, description="项目 ID。")],
        version: Annotated[int, Field(gt=0, description="项目内版本号。")],
    ) -> McpResult[VersionResult]:
        """Get one factor or backtest version."""
        with database_session_factory()() as session:
            user = current_user(session)
            if application == "factor":
                result = FactorVersionResponse.model_validate(get_factor_version(session, user.id, project_id, version))
            else:
                result = BacktestVersionResponse.model_validate(get_backtest_version(session, user.id, project_id, version))
            return McpResult(result=result)

    @server.tool(title="保存研究版本", annotations=WRITE)
    def save_version(
        application: Annotated[Literal["factor", "backtest"], Field(description="版本类型。")],
        project_id: Annotated[int, Field(gt=0, description="项目 ID。")],
        workflow_instance_id: Annotated[int, Field(gt=0, description="当前成功工作流的 workflow_instance_id。")],
        remark: Annotated[str, Field(max_length=512, description="版本备注。")] = "",
    ) -> McpResult[VersionResult]:
        """Save the current successful workflow as an immutable version."""
        with database_session_factory()() as session:
            user = current_user(session)
            if application == "factor":
                result = FactorVersionResponse.model_validate(create_factor_version(session, user.id, project_id, workflow_instance_id, remark.strip()))
            else:
                result = BacktestVersionResponse.model_validate(create_backtest_version(session, user.id, project_id, workflow_instance_id, remark.strip()))
            return McpResult(result=result)

    @server.tool(title="重命名研究版本", annotations=WRITE)
    def update_version(
        application: Annotated[Literal["factor", "backtest"], Field(description="版本类型。")],
        project_id: Annotated[int, Field(gt=0, description="项目 ID。")],
        version: Annotated[int, Field(gt=0, description="项目内版本号。")],
        remark: Annotated[str, Field(min_length=1, max_length=512, description="新的版本名称或备注。")],
    ) -> McpResult[VersionResult]:
        """Update the display remark of a Factor or Backtest version."""
        with database_session_factory()() as session:
            user = current_user(session)
            if application == "factor":
                validated = FactorVersionUpdate(remark=remark)
                result = FactorVersionResponse.model_validate(
                    update_factor_version(session, user.id, project_id, version, validated.remark)
                )
            else:
                validated = BacktestVersionUpdate(remark=remark)
                result = BacktestVersionResponse.model_validate(
                    update_backtest_version(session, user.id, project_id, version, validated.remark)
                )
            return McpResult(result=result)


__all__ = ["register_project_tools"]
