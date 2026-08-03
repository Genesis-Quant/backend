"""Live DolphinScheduler task operations; task information is not persisted."""

from typing import Any

from sqlalchemy.orm import Session

from core.apps.tasks.schemas import TaskAction
from core.apps.users.models import User
from core.apps.workflows.models import WorkflowInstance, WorkflowRun
from core.apps.workflows.services import (
    WorkflowGatewayService,
    task_information,
)
from core.scheduler.client import DolphinSchedulerClient, StreamedLog


class TaskGatewayService:
    def log(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
        task_instance_id: int,
        skip_line_num: int,
        limit: int,
    ) -> dict[str, Any]:
        with DolphinSchedulerClient() as client:
            workflow, _, task = self.find_accessible_task(
                session,
                user,
                workflow_instance_id,
                task_instance_id,
                client=client,
            )
            page = client.task_log(
                task_instance_id=task_instance_id,
                skip_line_num=skip_line_num,
                limit=limit,
            )
        return {
            "workflow_instance_id": workflow.workflow_instance_id,
            "task_instance_id": task_instance_id,
            "state": str(task.get("state") or "UNKNOWN"),
            **page,
        }

    def stream_log(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
        task_instance_id: int,
    ) -> StreamedLog:
        client = DolphinSchedulerClient()
        try:
            client.login()
            _, run, _ = self.find_accessible_task(
                session,
                user,
                workflow_instance_id,
                task_instance_id,
                client=client,
            )
            return client.stream_task_log(
                project_code=int(run.project_code or 0),
                task_instance_id=task_instance_id,
            )
        except Exception:
            client.session.close()
            raise

    def control(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
        task_instance_id: int,
        action: TaskAction,
    ) -> dict[str, Any]:
        with DolphinSchedulerClient() as client:
            _, run, task = self.find_accessible_task(
                session,
                user,
                workflow_instance_id,
                task_instance_id,
                client=client,
            )
            submission = client.execute_task_instance(
                int(run.project_code or 0),
                task_instance_id,
                action.value,
            )
        return {
            "action": action,
            "scheduler_submission": submission,
            "workflow_instance_id": workflow_instance_id,
            "task_instance_id": task_instance_id,
            "task": task,
        }

    @staticmethod
    def find_accessible_task(
        session: Session,
        user: User,
        workflow_instance_id: int,
        task_instance_id: int,
        client: DolphinSchedulerClient | None = None,
    ) -> tuple[WorkflowInstance, WorkflowRun, dict[str, Any]]:
        workflow, run = WorkflowGatewayService.find_accessible_workflow(
            session,
            user,
            workflow_instance_id,
        )
        if client is None:
            with DolphinSchedulerClient() as active_client:
                return TaskGatewayService.find_accessible_task(
                    session,
                    user,
                    workflow_instance_id,
                    task_instance_id,
                    client=active_client,
                )
        instances = client.process_instance_tasks(
            project_code=int(run.project_code or 0),
            process_instance_id=workflow.workflow_instance_id,
        )
        instance = next(
            (
                item
                for item in instances
                if int(item.get("id") or 0) == task_instance_id
            ),
            None,
        )
        if instance is None:
            raise FileNotFoundError(
                f"工作流 {workflow_instance_id} 中不存在 task instance: {task_instance_id}"
            )
        return workflow, run, task_information({}, instance)
