"""MCP workflow, task log, output, and control tools."""

from typing import Annotated, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.apps.backtest.services import backtest_result_files
from core.apps.factor.services import factor_result_files
from core.apps.query.services import query_result_files
from core.apps.tasks.schemas import TaskLogResponse
from core.apps.tasks.services import TaskGatewayService
from core.apps.workflows.schemas import WorkflowAction, WorkflowActionResponse, WorkflowInformation, WorkflowWorkspaceListResponse, WorkflowWorkspaceStatus
from core.apps.workflows.services import WorkflowGatewayService
from core.database.session import database_session_factory
from core.utils.results import ResultFile

from ..auth import current_user
from ..schemas import ApplicationName, CONTROL, McpResult, READ_ONLY, WorkflowOutputFile, WorkflowOutputs


def register_workflow_tools(server: MCPServer) -> None:
    """Register workflow polling, diagnostics, outputs, and control tools."""

    @server.tool(title="列出工作流", annotations=READ_ONLY)
    def list_workflows(
        application: Annotated[Literal["query", "factor", "backtest", "incremental"] | None, Field(description="可选应用筛选。")] = None,
        state: Annotated[Literal["active", "success", "failure"] | None, Field(description="可选状态分组筛选。")] = None,
        page: Annotated[int, Field(ge=1, description="页码，从 1 开始。")] = 1,
        page_size: Annotated[int, Field(ge=1, le=100, description="每页数量。")] = 20,
    ) -> McpResult[WorkflowWorkspaceListResponse]:
        """List workflow workspaces and their current attempts."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = WorkflowGatewayService().list(session, user, page, page_size, application, state)
            return McpResult(result=WorkflowWorkspaceListResponse.model_validate(result))

    @server.tool(title="获取工作空间状态", annotations=READ_ONLY)
    def get_workspace_status(
        workspace_id: Annotated[int, Field(gt=0, description="run 工具返回的 workspace_id。")],
    ) -> McpResult[WorkflowWorkspaceStatus]:
        """Poll the current attempt state."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = WorkflowGatewayService().workspace_status(session, user, workspace_id)
            return McpResult(result=WorkflowWorkspaceStatus.model_validate(result))

    @server.tool(title="获取工作流详情", annotations=READ_ONLY)
    def get_workflow_details(
        workflow_instance_id: Annotated[int, Field(gt=0, description="DolphinScheduler workflow instance ID。")],
    ) -> McpResult[WorkflowInformation]:
        """Get scheduler, request, task, and event details."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = WorkflowGatewayService().detail(session, user, workflow_instance_id)
            return McpResult(result=WorkflowInformation.model_validate(result))

    @server.tool(title="分页读取任务日志", annotations=READ_ONLY)
    def get_task_logs(
        workflow_instance_id: Annotated[int, Field(gt=0, description="工作流详情返回的 workflow instance ID。")],
        task_instance_id: Annotated[int, Field(gt=0, description="工作流详情中某个 Task 的 instance ID。")],
        skip_line_num: Annotated[int, Field(ge=0, description="首次使用 0，后续使用上次返回的 next_line_num。")] = 0,
        limit: Annotated[int, Field(ge=1, le=10000, description="本页最多返回的日志行数。")] = 1000,
    ) -> McpResult[TaskLogResponse]:
        """Read one authenticated task log page."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = TaskGatewayService().log(session, user, workflow_instance_id, task_instance_id, skip_line_num, limit)
            return McpResult(result=TaskLogResponse.model_validate(result))

    @server.tool(title="列出工作流输出", annotations=READ_ONLY)
    def list_workflow_outputs(
        application: Annotated[ApplicationName, Field(description="工作流应用：query、factor 或 backtest。")],
        workflow_instance_id: Annotated[int, Field(gt=0, description="成功且仍为当前 Attempt 的 workflow instance ID。")],
    ) -> McpResult[WorkflowOutputs]:
        """List generated Parquet outputs and download paths."""
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
                    download_path=f"/api/v1/{application}/workflows/{workflow_instance_id}/outputs/{item['name']}",
                )
                for item in items
            ]
            return McpResult(result=WorkflowOutputs(application=application, workflow_instance_id=workflow_instance_id, outputs=outputs))

    @server.tool(title="控制工作流", annotations=CONTROL)
    def control_workflow(
        workflow_instance_id: Annotated[int, Field(gt=0, description="需要控制的 workflow instance ID。")],
        action: Annotated[WorkflowAction, Field(description="stop、pause、resume、rerun 或 retry-failed。重跑会产生新的 Attempt。")],
    ) -> McpResult[WorkflowActionResponse]:
        """Perform a scheduler control action."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = WorkflowGatewayService().control(session, user, workflow_instance_id, action)
            return McpResult(result=WorkflowActionResponse.model_validate(result))


__all__ = ["register_workflow_tools"]
