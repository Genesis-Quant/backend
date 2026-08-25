"""MCP batch execution and backtest research tools."""

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field
from runtime import (
    BacktestParameters,
    FactorAnalysisParameters,
    OptimizationAlgorithm,
    OptimizationSettings,
)
from runtime.apps.backtest.api import compile_backtest_scripts

from core.apps.backtest.schemas import (
    BatchAnalysisType,
    BacktestOptimizationPage,
    BacktestOptimizationResponse,
    BatchResearchCreate,
    BatchResearchListResponse,
    BatchResearchResponse,
    FeeAnalysisCreate,
)
from core.apps.backtest.services import (
    create_batch_research,
    create_backtest_optimization as create_backtest_optimization_record,
    create_fee_analysis,
    get_batch_research,
    get_backtest_optimization as get_backtest_optimization_record,
    get_backtest_project,
    get_backtest_version,
    list_batch_research,
    list_backtest_optimizations as list_backtest_optimization_records,
    optimization_result_files,
    sensitivity_result_files,
    submit_backtest_batch,
)
from core.apps.factor.services import submit_factor_batch
from core.apps.schemas import BatchRunAccepted, BatchRunRequest
from core.database.session import database_session_factory

from ..auth import current_user
from ..contracts import validate_mcp_factor_parameters
from ..schemas import McpBatchRunItem, McpResult, READ_ONLY, WRITE, WorkflowOutputFile, WorkflowOutputs


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
                description=(
                    "每项包含唯一 client_id、可选 remark 和完整 parameters；"
                    "两阶段必须使用同一 stock_pool_member 股票池条件。"
                ),
            ),
        ],
    ) -> McpResult[list[BatchRunAccepted]]:
        """Submit Factor requests; successful results receive monotonic versions only when saved."""
        request = BatchRunRequest[FactorAnalysisParameters].model_validate({
            "items": [item.model_dump() for item in items],
        })
        for item in request.items:
            validate_mcp_factor_parameters(item.parameters)
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
        """Submit Backtests; successful results receive monotonic versions only when saved."""
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

    @server.tool(title="列出回测参数调优报告", annotations=READ_ONLY)
    def list_backtest_optimizations(
        project_id: Annotated[int, Field(gt=0, description="Backtest 项目 ID。")],
        version: Annotated[int, Field(gt=0, description="已保存版本号。")],
        page: Annotated[int, Field(ge=1, description="页码，从 1 开始。")] = 1,
        page_size: Annotated[int, Field(ge=1, le=100, description="每页数量。")] = 20,
    ) -> McpResult[BacktestOptimizationPage]:
        """List walk-forward parameter-optimization reports for one saved version."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = list_backtest_optimization_records(
                session,
                user,
                project_id,
                version,
                page,
                page_size,
            )
            return McpResult(
                result=BacktestOptimizationPage.model_validate(result)
            )

    @server.tool(title="创建回测参数调优报告", annotations=WRITE)
    def create_backtest_optimization(
        project_id: Annotated[int, Field(gt=0, description="Backtest 项目 ID。")],
        version: Annotated[int, Field(gt=0, description="作为来源的已保存版本号。")],
        parameter_space: Annotated[
            dict[str, list[int | float]],
            Field(description="待调优 params 字段及其 2 到 100 个有限数值候选。"),
        ],
        algorithms: Annotated[
            list[OptimizationAlgorithm],
            Field(min_length=1, description="需要比较的调优算法，不能重复。"),
        ],
        start_date: Annotated[str, Field(description="第一段样本外区间起点，YYYY-MM-DD。")],
        end_date: Annotated[str, Field(description="最后一段样本外区间终点，YYYY-MM-DD。")],
        lookback_period: Annotated[str, Field(description="训练窗口长度，例如 6M。")],
        holding_period: Annotated[str, Field(description="样本外持有窗口长度，例如 2W。")],
        repetitions: Annotated[int, Field(ge=1, le=100, description="每种算法的独立随机起点次数。")] = 1,
        evaluation_budget: Annotated[int, Field(ge=2, le=100, description="每个训练窗口最多评价的候选组合数。")] = 12,
        seed: Annotated[int, Field(ge=0, le=2_147_483_647, description="非负随机种子。")] = 20260815,
    ) -> McpResult[BacktestOptimizationResponse]:
        """Create one shared-data walk-forward parameter-optimization workflow."""
        settings = OptimizationSettings.model_validate({
            "parameter_space": parameter_space,
            "algorithms": algorithms,
            "start_date": start_date,
            "end_date": end_date,
            "lookback_period": lookback_period,
            "holding_period": holding_period,
            "repetitions": repetitions,
            "evaluation_budget": evaluation_budget,
            "seed": seed,
        })
        with database_session_factory()() as session:
            user = current_user(session)
            result = create_backtest_optimization_record(
                session,
                user,
                project_id,
                version,
                settings,
            )
            return McpResult(
                result=BacktestOptimizationResponse.model_validate(result)
            )

    @server.tool(title="获取回测参数调优报告", annotations=READ_ONLY)
    def get_backtest_optimization(
        optimization_id: Annotated[int, Field(gt=0, description="参数调优报告 ID。")],
    ) -> McpResult[BacktestOptimizationResponse]:
        """Get one parameter-optimization report and its current workflow state."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = get_backtest_optimization_record(
                session,
                user,
                optimization_id,
            )
            return McpResult(
                result=BacktestOptimizationResponse.model_validate(result)
            )

    @server.tool(title="列出回测参数调优输出", annotations=READ_ONLY)
    def list_backtest_optimization_outputs(
        optimization_id: Annotated[int, Field(gt=0, description="参数调优报告 ID。")],
    ) -> McpResult[WorkflowOutputs]:
        """List one Parquet path output for each selected optimization algorithm."""
        with database_session_factory()() as session:
            user = current_user(session)
            optimization = get_backtest_optimization_record(
                session,
                user,
                optimization_id,
            )
            workflow_instance_id = optimization["workflow_instance_id"]
            if workflow_instance_id is None:
                raise FileNotFoundError(
                    f"参数调优报告尚未关联工作流实例: {optimization_id}"
                )
            items = optimization_result_files(
                session,
                user.id,
                optimization_id,
            )
            outputs = [
                WorkflowOutputFile(
                    **item,
                    download_path=(
                        f"/api/v1/backtest/optimizations/{optimization_id}"
                        f"/outputs/{item['name']}"
                    ),
                )
                for item in items
            ]
            return McpResult(result=WorkflowOutputs(
                application="optimization",
                workflow_instance_id=workflow_instance_id,
                outputs=outputs,
            ))

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
        """Create one sensitivity workflow that reuses data for all parameter sets."""
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
        compile_backtest_scripts(validated_parameters[0])
        with database_session_factory()() as session:
            user = current_user(session)
            return McpResult(result=BatchResearchResponse.model_validate(create_batch_research(session, user, request)))

    @server.tool(title="获取策略批量研究", annotations=READ_ONLY)
    def get_backtest_research(
        research_id: Annotated[int, Field(gt=0, description="批量研究 ID。")],
    ) -> McpResult[BatchResearchResponse]:
        """Get the single shared-data workflow for one sensitivity study."""
        with database_session_factory()() as session:
            user = current_user(session)
            return McpResult(result=BatchResearchResponse.model_validate(get_batch_research(session, user, research_id)))

    @server.tool(title="列出策略研究结果", annotations=READ_ONLY)
    def list_backtest_research_outputs(
        research_id: Annotated[int, Field(gt=0, description="手续费或参数敏感性研究 ID。")],
    ) -> McpResult[WorkflowOutputs]:
        """List the shared sensitivity result Parquet and its authenticated download path."""
        with database_session_factory()() as session:
            user = current_user(session)
            research = get_batch_research(session, user, research_id)
            workflow_instance_id = research["workflow_instance_id"]
            if workflow_instance_id is None:
                raise FileNotFoundError(f"批量研究尚未关联工作流实例: {research_id}")
            items = sensitivity_result_files(session, user.id, research_id)
            outputs = [
                WorkflowOutputFile(
                    **item,
                    download_path=f"/api/v1/backtest/batch-research/{research_id}/outputs/{item['name']}",
                )
                for item in items
            ]
            return McpResult(result=WorkflowOutputs(
                application="sensitivity",
                workflow_instance_id=workflow_instance_id,
                outputs=outputs,
            ))

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
