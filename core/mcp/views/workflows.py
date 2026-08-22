"""MCP workflow, task log, output, and control tools."""

from typing import Annotated, Literal

from mcp.server import MCPServer
from pydantic import Field

from core.apps.backtest.services import backtest_result_files
from core.apps.factor.services import factor_result_files
from core.apps.query.services import query_result_files
from core.apps.tasks.schemas import TaskAction, TaskActionResponse, TaskLogResponse
from core.apps.tasks.services import TaskGatewayService
from core.apps.workflows.schemas import (
    WorkflowAction,
    WorkflowActionResponse,
    WorkflowAttemptInformation,
    WorkflowAttemptListResponse,
    WorkflowInformation,
    WorkflowStatusInformation,
    WorkflowTasks,
    WorkflowWorkspaceListResponse,
    WorkflowWorkspaceStatus,
)
from core.apps.workflows.services import WorkflowGatewayService
from core.database.session import database_session_factory
from core.utils.results import ResultFile

from ..auth import current_user
from ..schemas import CONTROL, McpResult, READ_ONLY, TaskLogDownload, WorkflowOutputFile, WorkflowOutputs


def register_workflow_tools(server: MCPServer) -> None:
    """Register workflow polling, diagnostics, outputs, and control tools."""

    @server.tool(title="列出工作流", annotations=READ_ONLY)
    def list_workflows(
        application: Annotated[Literal["query", "factor", "backtest", "optimization", "sensitivity", "incremental"] | None, Field(description="可选应用筛选。")] = None,
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

    @server.tool(title="获取工作流状态", annotations=READ_ONLY)
    def get_workflow_status(
        workflow_instance_id: Annotated[int, Field(gt=0, description="DolphinScheduler workflow instance ID。")],
    ) -> McpResult[WorkflowStatusInformation]:
        """Synchronize and return one workflow instance's lightweight status."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = WorkflowGatewayService().status(session, user, workflow_instance_id)
            return McpResult(result=WorkflowStatusInformation.model_validate(result))

    @server.tool(title="列出工作空间运行记录", annotations=READ_ONLY)
    def list_workflow_attempts(
        workspace_id: Annotated[int, Field(gt=0, description="项目、版本或 run 工具返回的 workspace_id。")],
        page: Annotated[int, Field(ge=1, description="页码，从 1 开始；运行记录按创建时间倒序返回。")] = 1,
        page_size: Annotated[int, Field(ge=1, le=50, description="每页运行记录数量。")] = 20,
        include_tasks: Annotated[
            bool,
            Field(
                description=(
                    "是否同时读取每次运行的 Task；默认关闭以避免分页列表"
                    "对 DolphinScheduler 发起逐条请求。诊断单次运行时优先调用 list_workflow_tasks。"
                )
            ),
        ] = False,
    ) -> McpResult[WorkflowAttemptListResponse]:
        """List current and historical attempts in one workspace."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = WorkflowGatewayService().attempts(
                session,
                user,
                workspace_id,
                page,
                page_size,
                include_tasks,
            )
            return McpResult(result=WorkflowAttemptListResponse.model_validate(result))

    @server.tool(title="获取一次运行记录", annotations=READ_ONLY)
    def get_workflow_attempt(
        attempt_id: Annotated[int, Field(gt=0, description="list_workflow_attempts 返回的 attempt_id。")],
    ) -> McpResult[WorkflowAttemptInformation]:
        """Get one attempt including its submitted input JSON and lifecycle events."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = WorkflowGatewayService().attempt_detail(session, user, attempt_id)
            return McpResult(result=WorkflowAttemptInformation.model_validate(result))

    @server.tool(title="获取工作流详情", annotations=READ_ONLY)
    def get_workflow_details(
        workflow_instance_id: Annotated[int, Field(gt=0, description="DolphinScheduler workflow instance ID。")],
    ) -> McpResult[WorkflowInformation]:
        """Get scheduler, request, task, and event details."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = WorkflowGatewayService().detail(session, user, workflow_instance_id)
            return McpResult(result=WorkflowInformation.model_validate(result))

    @server.tool(title="列出工作流任务", annotations=READ_ONLY)
    def list_workflow_tasks(
        workflow_instance_id: Annotated[int, Field(gt=0, description="DolphinScheduler workflow instance ID。")],
    ) -> McpResult[WorkflowTasks]:
        """Return the current task instances without the full workflow payload."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = WorkflowGatewayService().tasks(session, user, workflow_instance_id)
            return McpResult(result=WorkflowTasks.model_validate(result))

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

    @server.tool(title="获取完整任务日志下载地址", annotations=READ_ONLY)
    def get_task_log_download(
        workflow_instance_id: Annotated[int, Field(gt=0, description="工作流实例 ID。")],
        task_instance_id: Annotated[int, Field(gt=0, description="属于该工作流的 Task instance ID。")],
    ) -> McpResult[TaskLogDownload]:
        """Authorize and return the same complete-log download path used by the web page."""
        with database_session_factory()() as session:
            user = current_user(session)
            TaskGatewayService.find_accessible_task(session, user, workflow_instance_id, task_instance_id)
            return McpResult(result=TaskLogDownload(
                workflow_instance_id=workflow_instance_id,
                task_instance_id=task_instance_id,
                download_path=(
                    f"/api/v1/tasks/{task_instance_id}/logs/download"
                    f"?workflow_instance_id={workflow_instance_id}"
                ),
            ))

    @server.tool(title="将任务标记为成功", annotations=CONTROL)
    def force_success_task(
        workflow_instance_id: Annotated[int, Field(gt=0, description="工作流实例 ID。")],
        task_instance_id: Annotated[int, Field(gt=0, description="属于该工作流的 Task instance ID。")],
    ) -> McpResult[TaskActionResponse]:
        """Force one owned task to success; retry the failed workflow afterwards, and required outputs are still validated."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = TaskGatewayService().control(
                session,
                user,
                workflow_instance_id,
                task_instance_id,
                TaskAction.FORCE_SUCCESS,
            )
            return McpResult(result=TaskActionResponse.model_validate(result))

    @server.tool(title="列出工作流输出", annotations=READ_ONLY)
    def list_workflow_outputs(
        application: Annotated[Literal["query", "factor", "backtest"], Field(description="工作流应用。")],
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
        action: Annotated[
            WorkflowAction,
            Field(
                description=(
                    "控制动作；重跑会产生新的 Attempt；暂停不打断正在运行的 Task，"
                    "resume 仅能恢复已进入 PAUSE 的工作流；READY_PAUSE 是调度器等待暂停的中间状态，不能恢复。"
                )
            ),
        ],
    ) -> McpResult[WorkflowActionResponse]:
        """Perform a scheduler control action."""
        with database_session_factory()() as session:
            user = current_user(session)
            result = WorkflowGatewayService().control(session, user, workflow_instance_id, action)
            return McpResult(result=WorkflowActionResponse.model_validate(result))


__all__ = ["register_workflow_tools"]
