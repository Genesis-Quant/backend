"""MCP batch execution and backtest research tools."""

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field
from runtime import BacktestParameters, FactorAnalysisParameters
from runtime.apps.backtest.api import compile_backtest_scripts

from core.apps.backtest.schemas import (
    BatchAnalysisType,
    BatchResearchCreate,
    BatchResearchListResponse,
    BatchResearchResponse,
    FeeAnalysisCreate,
)
from core.apps.backtest.services import (
    calculate_batch_research_results,
    create_batch_research,
    create_fee_analysis,
    get_batch_research,
    get_backtest_project,
    get_backtest_version,
    list_batch_research,
    submit_backtest_batch,
)
from core.apps.factor.services import submit_factor_batch
from core.apps.schemas import BatchRunAccepted, BatchRunRequest
from core.database.session import database_session_factory

from ..auth import current_user
from ..schemas import McpBatchRunItem, McpResult, READ_ONLY, WRITE


def register_batch_tools(server: MCPServer) -> None:
    """Register the batch operations used by Factor and Backtest pages."""

    @server.tool(title="批量执行因子分析", annotations=WRITE)
    def run_factor_batch(
        project_id: Annotated[int, Field(gt=0, description="已存在的 Factor 项目 ID。")],
        items: Annotated[
            list[McpBatchRunItem],
            Field(
                min_length=1,
                max_length=100,
                description="每项必须包含唯一 client_id、可选 remark 和完整 FactorAnalysisParameters parameters。",
            ),
        ],
    ) -> McpResult[list[BatchRunAccepted]]:
        """Submit up to 100 Factor requests and auto-save each successful result as a version."""
        request = BatchRunRequest[FactorAnalysisParameters].model_validate({
            "items": [item.model_dump() for item in items],
        })
        with database_session_factory()() as session:
            user = current_user(session)
            result = submit_factor_batch(session, user.id, project_id, request.items)
            return McpResult(result=[BatchRunAccepted.model_validate(item) for item in result])

    @server.tool(title="批量执行策略回测", annotations=WRITE)
    def run_backtest_batch(
        project_id: Annotated[int, Field(gt=0, description="已存在的 Backtest 项目 ID。")],
        items: Annotated[
            list[McpBatchRunItem],
            Field(
                min_length=1,
                max_length=100,
                description="每项必须包含唯一 client_id、可选 remark 和完整 BacktestParameters parameters。",
            ),
        ],
    ) -> McpResult[list[BatchRunAccepted]]:
        """Submit up to 100 Backtests and auto-save each successful result as a version."""
        request = BatchRunRequest[BacktestParameters].model_validate({
            "items": [item.model_dump() for item in items],
        })
        with database_session_factory()() as session:
            user = current_user(session)
            get_backtest_project(session, user.id, project_id)
        for item in request.items:
            compile_backtest_scripts(item.parameters)
        with database_session_factory()() as session:
            user = current_user(session)
            result = submit_backtest_batch(session, user.id, project_id, request.items)
            return McpResult(result=[BatchRunAccepted.model_validate(item) for item in result])

    @server.tool(title="列出策略批量研究", annotations=READ_ONLY)
    def list_backtest_researches(
        page: Annotated[int, Field(ge=1, description="页码，从 1 开始。")] = 1,
        page_size: Annotated[int, Field(ge=1, le=100, description="每页数量。")] = 20,
        project_id: Annotated[int | None, Field(gt=0, description="可选 Backtest 项目 ID。")] = None,
        version: Annotated[int | None, Field(gt=0, description="可选项目版本号。")] = None,
        analysis_type: Annotated[
            Literal["fee_analysis", "sensitivity"] | None,
            Field(description="可选研究类型。"),
        ] = None,
    ) -> McpResult[BatchResearchListResponse]:
        """List fee or parameter-sensitivity studies owned by the current user."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = list_batch_research(
                session,
                user,
                page,
                page_size,
                project_id=project_id,
                version=version,
                analysis_type=analysis_type,
            )
            return McpResult(result=BatchResearchListResponse.model_validate(result))

    @server.tool(title="创建策略批量研究", annotations=WRITE)
    def create_backtest_research(
        analysis_type: Annotated[Literal["fee_analysis", "sensitivity"], Field(description="批量研究类型。")],
        project_id: Annotated[int, Field(gt=0, description="Backtest 项目 ID。")],
        version: Annotated[int, Field(gt=0, description="作为基准的已保存版本号。")],
        parameter_sets: Annotated[
            list[dict[str, Any]],
            Field(
                min_length=1,
                max_length=100,
                description="1 到 100 份完整 BacktestParameters；不是局部 override。",
            ),
        ],
        description: Annotated[str, Field(max_length=512, description="研究备注。")] = "",
    ) -> McpResult[BatchResearchResponse]:
        """Create a fee or sensitivity study from complete Backtest parameter sets."""
        validated_parameters = [BacktestParameters.model_validate(parameters) for parameters in parameter_sets]
        request = BatchResearchCreate.model_validate({
            "analysis_type": BatchAnalysisType(analysis_type),
            "project_id": project_id,
            "version": version,
            "description": description,
            "items": [{"parameters": parameters.model_dump(mode="json")} for parameters in validated_parameters],
        })
        with database_session_factory()() as session:
            user = current_user(session)
            source = get_backtest_version(session, user.id, project_id, version)
            if not source["saved"]:
                raise FileNotFoundError(f"策略回测版本不存在: {project_id}/v{version}")
        for parameters in validated_parameters:
            compile_backtest_scripts(parameters)
        with database_session_factory()() as session:
            user = current_user(session)
            return McpResult(result=BatchResearchResponse.model_validate(create_batch_research(session, user, request)))

    @server.tool(title="获取策略批量研究", annotations=READ_ONLY)
    def get_backtest_research(
        research_id: Annotated[int, Field(gt=0, description="批量研究 ID。")],
    ) -> McpResult[BatchResearchResponse]:
        """Get every execution and metric in one batch research."""
        with database_session_factory()() as session:
            user = current_user(session)
            return McpResult(result=BatchResearchResponse.model_validate(get_batch_research(session, user, research_id)))

    @server.tool(title="计算策略批量研究结果", annotations=WRITE)
    def calculate_backtest_research(
        research_id: Annotated[int, Field(gt=0, description="工作流已结束的批量研究 ID。")],
    ) -> McpResult[BatchResearchResponse]:
        """Collect successful Parquet outputs and calculate metrics for one research."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = calculate_batch_research_results(session, user, research_id)
            return McpResult(result=BatchResearchResponse.model_validate(result))

    @server.tool(title="创建手续费分析", annotations=WRITE)
    def create_backtest_fee_analysis(
        project_id: Annotated[int, Field(gt=0, description="Backtest 项目 ID。")],
        version: Annotated[int, Field(gt=0, description="作为基准的已保存版本号。")],
        rates: Annotated[
            list[float],
            Field(min_length=1, max_length=100, description="手续费率列表，每项位于 0 到 1；服务端去重并排序。"),
        ],
    ) -> McpResult[BatchResearchResponse]:
        """Create a commission-rate grid from one saved Backtest version."""
        request = FeeAnalysisCreate.model_validate({"rates": rates})
        with database_session_factory()() as session:
            user = current_user(session)
            result = create_fee_analysis(session, user, project_id, version, request)
            return McpResult(result=BatchResearchResponse.model_validate(result))


__all__ = ["register_batch_tools"]
