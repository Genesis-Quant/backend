"""Administrator operations backed by Arena and DolphinScheduler."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import DolphinSchedulerSettings
from core.apps.tasks.services import APPLICATION_MODELS, TaskGatewayService
from core.apps.users.models import User
from core.scheduler.client import DolphinSchedulerClient
from core.scheduler.domain import FAILURE_STATES, TERMINAL_STATES
from core.scheduler.errors import DolphinSchedulerError
from core.scheduler.workflows import ensure_all_workflows


class AdminService:
    def overview(self, session: Session) -> dict[str, Any]:
        return {
            "users": self.user_summary(session),
            "tasks": self.task_summary(session),
            "scheduler": self.scheduler_overview(),
        }

    @staticmethod
    def users(session: Session) -> list[User]:
        return list(session.scalars(select(User).order_by(User.created_at, User.id)))

    @staticmethod
    def update_user(session: Session, actor: User, user_id: int, is_admin: bool) -> User:
        user = session.get(User, user_id)
        if user is None:
            raise FileNotFoundError(f"用户不存在: {user_id}")
        if user.id == actor.id and not is_admin:
            raise RuntimeError("不能取消自己的管理员权限")
        user.is_admin = is_admin
        session.commit()
        session.refresh(user)
        return user

    @staticmethod
    def ensure_workflows() -> dict[str, Any]:
        return ensure_all_workflows()

    @staticmethod
    def run_incremental_update(session: Session, user_id: int) -> dict[str, Any]:
        result = TaskGatewayService().submit_incremental(session, user_id)
        task = result["task"]
        return {
            "message": "增量更新工作流已提交",
            "job_id": result["job_id"],
            "record_id": task.id,
            "task_id": task.task_id,
            "process_instance_id": task.process_instance_id,
            "project_code": int(task.project_code),
            "process_definition_code": int(task.process_definition_code),
            "scheduler_submission": result["scheduler_submission"],
        }

    @staticmethod
    def user_summary(session: Session) -> dict[str, int]:
        return {
            "total": int(session.scalar(select(func.count()).select_from(User)) or 0),
            "administrators": int(
                session.scalar(
                    select(func.count()).select_from(User).where(User.is_admin.is_(True))
                )
                or 0
            ),
        }

    @staticmethod
    def task_summary(session: Session) -> dict[str, int]:
        result = {"total": 0, "active": 0, "success": 0, "failure": 0}
        for _, model in APPLICATION_MODELS:
            result["total"] += count_tasks(session, model)
            result["active"] += count_tasks(session, model, model.state.not_in(TERMINAL_STATES))
            result["success"] += count_tasks(
                session,
                model,
                model.state.in_(("SUCCESS", "FORCED_SUCCESS")),
            )
            result["failure"] += count_tasks(
                session,
                model,
                model.state.in_((*FAILURE_STATES, "SUBMIT_FAILED")),
            )
        return result

    @staticmethod
    def scheduler_overview() -> dict[str, Any]:
        base: dict[str, Any] = {
            "available": False,
            "project_name": DolphinSchedulerSettings.PROJECT_NAME,
            "workflows": [],
            "task_groups": [],
            "worker_groups": [],
            "workers": [],
            "recent_instances": [],
        }
        try:
            with DolphinSchedulerClient() as client:
                project_code = client.project_code(DolphinSchedulerSettings.PROJECT_NAME)
                if project_code is None:
                    raise DolphinSchedulerError(
                        f"DolphinScheduler 项目不存在: {DolphinSchedulerSettings.PROJECT_NAME}"
                    )
                workflow_names = [
                    *DolphinSchedulerSettings.APPLICATION_WORKFLOW_NAMES.values(),
                    DolphinSchedulerSettings.WORKFLOW_NAME,
                ]
                definitions = [
                    definition
                    for name in workflow_names
                    if (definition := client.process_definition(project_code, name)) is not None
                ]
                base.update(
                    {
                        "available": True,
                        "project_code": project_code,
                        "workflows": [workflow_information(item) for item in definitions],
                        "task_groups": [task_group_information(item) for item in client.task_groups(project_code=project_code)],
                        "worker_groups": client.worker_groups(),
                        "workers": [worker_information(item) for item in client.workers()],
                        "recent_instances": [
                            process_instance_information(item)
                            for item in client.process_instances(
                                project_code=project_code,
                                page_size=20,
                            )
                        ],
                    }
                )
        except DolphinSchedulerError as error:
            base["error"] = str(error)
        return base


def count_tasks(session: Session, model: type[Any], *conditions: Any) -> int:
    statement = select(func.count()).select_from(model)
    if conditions:
        statement = statement.where(*conditions)
    return int(session.scalar(statement) or 0)


def workflow_information(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(definition.get("name") or ""),
        "code": int(definition.get("code") or 0),
        "version": int(definition.get("version") or 0),
        "release_state": str(definition.get("releaseState") or "UNKNOWN"),
        "execution_type": optional_string(definition.get("executionType")),
        "updated_at": definition.get("updateTime"),
    }


def task_group_information(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(group.get("id") or 0),
        "name": str(group.get("name") or ""),
        "group_size": int(group.get("groupSize") or 0),
        "use_size": int(group.get("useSize") or 0),
        "status": str(group.get("status") or "UNKNOWN"),
        "description": str(group.get("description") or ""),
    }


def worker_information(worker: dict[str, Any]) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    try:
        resources = json.loads(worker.get("resInfo") or "{}")
    except (TypeError, json.JSONDecodeError):
        pass
    return {
        "id": int(worker.get("id") or 0),
        "host": str(worker.get("host") or ""),
        "port": int(worker.get("port") or 0),
        "status": str(resources.get("serverStatus") or "UNKNOWN"),
        "cpu_usage": optional_float(resources.get("cpuUsage")),
        "memory_usage": optional_float(resources.get("memoryUsage")),
        "thread_pool_usage": optional_float(resources.get("threadPoolUsage")),
        "last_heartbeat_at": worker.get("lastHeartbeatTime"),
    }


def process_instance_information(instance: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(instance.get("id") or 0),
        "name": str(instance.get("name") or ""),
        "workflow_code": int(instance.get("processDefinitionCode") or 0),
        "state": str(instance.get("state") or "UNKNOWN"),
        "worker_group": str(instance.get("workerGroup") or ""),
        "started_at": instance.get("startTime"),
        "finished_at": instance.get("endTime"),
        "duration": optional_string(instance.get("duration")),
    }


def optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
