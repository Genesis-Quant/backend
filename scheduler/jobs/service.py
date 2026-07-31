"""Complete Arena job submission, control, and status tracking service."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from scheduler.clients import DolphinSchedulerClient, DownloadedLog
from scheduler.config import DolphinSchedulerSettings
from scheduler.definitions import ensure_workflow_definition
from scheduler.domain import (
    APPLICATIONS,
    TERMINAL_STATES,
    ApplicationName,
    JobAction,
    JobKind,
    TaskAction,
)
from scheduler.errors import (
    DolphinSchedulerError,
    JobStateError,
    JobValidationError,
)
from scheduler.jobs.store import SharedJobStore

ClientFactory = Callable[[], DolphinSchedulerClient]


class SchedulerService:
    """Coordinate Arena metadata with DolphinScheduler runtime state."""

    def __init__(
        self,
        settings: DolphinSchedulerSettings | None = None,
        *,
        store: SharedJobStore | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.settings = settings or DolphinSchedulerSettings.from_environment()
        self.store = store or SharedJobStore(self.settings.shared_dir)
        self.client_factory = client_factory or (
            lambda: DolphinSchedulerClient(self.settings)
        )

    def submit_application(
        self,
        application: ApplicationName | str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if application not in APPLICATIONS:
            raise JobValidationError(f"不支持的应用: {application}")
        application = cast(ApplicationName, application)
        metadata = self.store.create(application, payload)
        return self._submit(
            metadata,
            application,
            {
                "input_file": str(metadata["input_file"]),
                "job_id": metadata["job_id"],
            },
        )

    def submit_incremental_update(self) -> dict[str, Any]:
        metadata = self.store.create_workflow_job("incremental-update")
        return self._submit(
            metadata,
            "incremental-update",
            {"job_id": metadata["job_id"]},
        )

    def get_job(
        self,
        job_id: str,
        *,
        include_tasks: bool = True,
    ) -> dict[str, Any]:
        metadata = self.store.load(job_id)
        try:
            instance, tasks = self._synchronize(
                metadata,
                include_tasks=include_tasks,
            )
        except DolphinSchedulerError as error:
            metadata["scheduler_error"] = str(error)
            self.store.save(metadata)
            instance, tasks = None, []
        result = self.store.response(metadata)
        result["process_instance"] = (
            _process_summary(instance) if instance is not None else None
        )
        if include_tasks:
            summarized_tasks = [_task_summary(task) for task in tasks]
            result["tasks"] = summarized_tasks
            result["task_summary"] = _tasks_summary(summarized_tasks)
        return result

    def list_jobs(
        self,
        *,
        application: str | None = None,
        state: str | None = None,
        limit: int = 100,
        refresh: bool = False,
    ) -> dict[str, Any]:
        jobs = self.store.list(
            application=application,
            state=state,
            limit=limit,
        )
        if refresh:
            jobs = [
                self.get_job(job["job_id"], include_tasks=False)
                for job in jobs
            ]
        else:
            jobs = [self.store.response(job) for job in jobs]
        return {"total": len(jobs), "jobs": jobs}

    def get_tasks(self, job_id: str) -> dict[str, Any]:
        metadata = self.store.load(job_id)
        instance, tasks = self._synchronize(metadata, include_tasks=True)
        summarized_tasks = [_task_summary(task) for task in tasks]
        return {
            "job_id": metadata["job_id"],
            "process_instance": (
                _process_summary(instance) if instance is not None else None
            ),
            "summary": _tasks_summary(summarized_tasks),
            "tasks": summarized_tasks,
        }

    def get_task_log(
        self,
        job_id: str,
        task_instance_id: int,
        *,
        skip_line_num: int = 0,
        limit: int = 1000,
    ) -> dict[str, Any]:
        metadata, task = self._owned_task(job_id, task_instance_id)
        with self.client_factory() as client:
            page = client.task_log(
                task_instance_id=task_instance_id,
                skip_line_num=skip_line_num,
                limit=limit,
            )
        return {
            "job_id": metadata["job_id"],
            "task_instance_id": task_instance_id,
            "task_name": task.get("name"),
            "state": task.get("state"),
            **page,
        }

    def download_task_log(
        self,
        job_id: str,
        task_instance_id: int,
    ) -> DownloadedLog:
        metadata, _ = self._owned_task(job_id, task_instance_id)
        with self.client_factory() as client:
            return client.download_task_log(
                project_code=int(metadata["project_code"]),
                task_instance_id=task_instance_id,
            )

    def get_task(
        self,
        job_id: str,
        task_instance_id: int,
    ) -> dict[str, Any]:
        metadata, task = self._owned_task(job_id, task_instance_id)
        return {
            "job_id": metadata["job_id"],
            "task": _task_summary(task),
        }

    def control_task(
        self,
        job_id: str,
        task_instance_id: int,
        action: TaskAction,
    ) -> dict[str, Any]:
        metadata, task = self._owned_task(job_id, task_instance_id)
        state = str(task.get("state"))
        if action is TaskAction.STOP and state in TERMINAL_STATES:
            raise JobStateError(f"{state} 状态不能停止")
        if action is TaskAction.FORCE_SUCCESS and state == "SUCCESS":
            raise JobStateError("SUCCESS 状态不需要强制成功")
        with self.client_factory() as client:
            submission = client.execute_task_instance(
                project_code=int(metadata["project_code"]),
                task_instance_id=task_instance_id,
                action=action.value,
            )
        self.store.append_event(
            metadata,
            "TASK_CONTROL_REQUESTED",
            action=action.value,
            task_instance_id=task_instance_id,
        )
        return {
            "job_id": metadata["job_id"],
            "task_instance_id": task_instance_id,
            "action": action.value,
            "scheduler_submission": submission,
        }

    def control_job(
        self,
        job_id: str,
        action: JobAction,
    ) -> dict[str, Any]:
        metadata = self.store.load(job_id)
        instance, _ = self._synchronize(metadata, include_tasks=False)
        if instance is None or not metadata.get("process_instance_id"):
            raise JobStateError("任务尚未生成 DolphinScheduler 工作流实例")
        state = str(instance.get("state") or metadata.get("scheduler_state"))
        self._validate_action(action, state)
        process_instance_id = int(metadata["process_instance_id"])
        with self.client_factory() as client:
            submission = client.execute_process_instance(
                project_code=int(metadata["project_code"]),
                process_instance_id=process_instance_id,
                execute_type=action.execute_type,
            )
        metadata["last_action"] = action.value
        metadata["scheduler_submission"] = submission
        if action.creates_attempt:
            history = metadata.setdefault("process_instance_history", [])
            if process_instance_id not in history:
                history.append(process_instance_id)
            metadata["process_instance_id"] = None
            metadata["scheduler_state"] = None
            metadata["state"] = "SUBMITTED"
        self.store.append_event(
            metadata,
            "CONTROL_REQUESTED",
            action=action.value,
            process_instance_id=process_instance_id,
        )
        return self.store.response(metadata)

    def audit_logs(self, **filters: Any) -> dict[str, Any]:
        with self.client_factory() as client:
            return client.audit_logs(**filters)

    def audit_log_types(self) -> dict[str, Any]:
        with self.client_factory() as client:
            return {
                "operations": client.audit_operation_types(),
                "models": client.audit_model_types(),
            }

    def _submit(
        self,
        metadata: dict[str, Any],
        workflow: JobKind,
        start_params: dict[str, str],
    ) -> dict[str, Any]:
        try:
            project_code, definition = ensure_workflow_definition(
                workflow,
                self.settings,
            )
            metadata.update(
                {
                    "state": "SUBMITTING",
                    "project_code": project_code,
                    "process_definition_code": int(definition["code"]),
                    "workflow_name": definition["name"],
                }
            )
            self.store.save(metadata)
            with self.client_factory() as client:
                submission = client.start_process_instance(
                    project_code=project_code,
                    process_definition_code=int(definition["code"]),
                    start_params=start_params,
                )
            metadata["state"] = "SUBMITTED"
            metadata["scheduler_submission"] = submission
            self.store.append_event(metadata, "SUBMITTED")
        except (DolphinSchedulerError, OSError) as error:
            metadata["state"] = "SUBMIT_FAILED"
            metadata["error"] = str(error)
            self.store.append_event(
                metadata,
                "SUBMIT_FAILED",
                error=str(error),
            )
            raise
        return self.store.response(metadata)

    def _synchronize(
        self,
        metadata: dict[str, Any],
        *,
        include_tasks: bool,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        project_code = metadata.get("project_code")
        definition_code = metadata.get("process_definition_code")
        if not project_code or not definition_code:
            return None, []
        with self.client_factory() as client:
            instance = self._locate_process_instance(
                client,
                metadata,
                project_code=int(project_code),
                process_definition_code=int(definition_code),
            )
            tasks = []
            if instance is not None and include_tasks:
                tasks = client.process_instance_tasks(
                    project_code=int(project_code),
                    process_instance_id=int(instance["id"]),
                )
        if instance is not None:
            old_state = metadata.get("scheduler_state")
            new_state = instance.get("state")
            metadata["process_instance_id"] = int(instance["id"])
            metadata["scheduler_state"] = new_state
            metadata["state"] = new_state or metadata["state"]
            metadata.pop("scheduler_error", None)
            if new_state != old_state:
                self.store.append_event(
                    metadata,
                    "STATE_CHANGED",
                    previous_state=old_state,
                    state=new_state,
                    process_instance_id=int(instance["id"]),
                )
            else:
                self.store.save(metadata)
        return instance, tasks

    @staticmethod
    def _locate_process_instance(
        client: DolphinSchedulerClient,
        metadata: dict[str, Any],
        *,
        project_code: int,
        process_definition_code: int,
    ) -> dict[str, Any] | None:
        instance_id = metadata.get("process_instance_id")
        if instance_id:
            return client.process_instance(project_code, int(instance_id))

        markers = [
            str(metadata["job_id"]),
            str(metadata.get("input_file") or ""),
        ]
        markers = [marker for marker in markers if marker]
        for page_no in range(1, 21):
            instances = client.process_instances(
                project_code=project_code,
                process_definition_code=process_definition_code,
                page_no=page_no,
                page_size=100,
            )
            for instance in sorted(
                instances,
                key=lambda value: int(value.get("id", 0)),
                reverse=True,
            ):
                serialized = json.dumps(instance, ensure_ascii=False)
                if any(marker in serialized for marker in markers):
                    return instance
            if len(instances) < 100:
                break
        return None

    def _owned_task(
        self,
        job_id: str,
        task_instance_id: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        metadata = self.store.load(job_id)
        _, tasks = self._synchronize(metadata, include_tasks=True)
        for task in tasks:
            if int(task["id"]) == task_instance_id:
                return metadata, task
        raise FileNotFoundError(
            f"任务 {job_id} 不包含 task instance {task_instance_id}"
        )

    @staticmethod
    def _validate_action(action: JobAction, state: str) -> None:
        if action in {JobAction.STOP, JobAction.PAUSE} and state in TERMINAL_STATES:
            raise JobStateError(f"{state} 状态不能执行 {action.value}")
        if action is JobAction.RESUME and state != "PAUSE":
            raise JobStateError(f"{state} 状态不能恢复执行")
        if action is JobAction.RETRY_FAILED and state != "FAILURE":
            raise JobStateError(f"{state} 状态不能从失败节点续跑")
        if action is JobAction.RERUN and state not in TERMINAL_STATES:
            raise JobStateError(f"{state} 状态不能整单重跑")


def _process_summary(instance: dict[str, Any]) -> dict[str, Any]:
    start_time = instance.get("startTime")
    end_time = instance.get("endTime")
    return {
        "id": instance.get("id"),
        "name": instance.get("name"),
        "state": instance.get("state"),
        "command_type": instance.get("commandType"),
        "command_start_time": instance.get("commandStartTime"),
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": _duration_seconds(start_time, end_time),
        "run_times": instance.get("runTimes"),
        "host": instance.get("host"),
        "worker_group": instance.get("workerGroup"),
        "failure_strategy": instance.get("failureStrategy"),
        "state_history": instance.get("stateHistory") or [],
    }


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    start_time = task.get("startTime")
    end_time = task.get("endTime")
    return {
        "id": task.get("id"),
        "name": task.get("name"),
        "task_type": task.get("taskType"),
        "state": task.get("state"),
        "submit_time": task.get("submitTime"),
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": _duration_seconds(start_time, end_time),
        "host": task.get("host"),
        "retry_times": task.get("retryTimes"),
        "max_retry_times": task.get("maxRetryTimes"),
        "worker_group": task.get("workerGroup"),
        "log_path": task.get("logPath"),
        "task_complete": task.get("taskComplete"),
    }


def _tasks_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    states = Counter(str(task.get("state")) for task in tasks)
    started = [task["start_time"] for task in tasks if task.get("start_time")]
    ended = [task["end_time"] for task in tasks if task.get("end_time")]
    return {
        "total": len(tasks),
        "states": dict(sorted(states.items())),
        "started_at": min(started) if started else None,
        "ended_at": max(ended) if ended else None,
        "duration_seconds": _duration_seconds(
            min(started) if started else None,
            max(ended) if ended else None,
        ),
    }


def _duration_seconds(start: Any, end: Any) -> float | None:
    if not start or not end:
        return None
    try:
        started = datetime.fromisoformat(str(start))
        ended = datetime.fromisoformat(str(end))
    except ValueError:
        return None
    return round((ended - started).total_seconds(), 3)


def _service(
    settings: DolphinSchedulerSettings | None = None,
) -> SchedulerService:
    return SchedulerService(settings)


def create_and_submit_application_job(
    application: ApplicationName | str,
    payload: dict[str, Any],
    settings: DolphinSchedulerSettings | None = None,
) -> dict[str, Any]:
    return _service(settings).submit_application(application, payload)


def create_and_submit_incremental_update(
    settings: DolphinSchedulerSettings | None = None,
) -> dict[str, Any]:
    return _service(settings).submit_incremental_update()


def get_application_job(
    job_id: str,
    settings: DolphinSchedulerSettings | None = None,
) -> dict[str, Any]:
    return _service(settings).get_job(job_id)


def list_application_jobs(
    settings: DolphinSchedulerSettings | None = None,
    **filters: Any,
) -> dict[str, Any]:
    return _service(settings).list_jobs(**filters)


def control_application_job(
    job_id: str,
    action: JobAction,
    settings: DolphinSchedulerSettings | None = None,
) -> dict[str, Any]:
    return _service(settings).control_job(job_id, action)


def get_application_task_log(
    job_id: str,
    task_instance_id: int,
    settings: DolphinSchedulerSettings | None = None,
    **pagination: Any,
) -> dict[str, Any]:
    return _service(settings).get_task_log(
        job_id,
        task_instance_id,
        **pagination,
    )


def download_application_task_log(
    job_id: str,
    task_instance_id: int,
    settings: DolphinSchedulerSettings | None = None,
) -> DownloadedLog:
    return _service(settings).download_task_log(job_id, task_instance_id)
