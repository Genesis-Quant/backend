"""Task submission, synchronization, authorization, logs, control, and polling."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from core.apps.backtest.models import BacktestTask
from core.apps.factor.models import FactorTask
from core.apps.incremental.models import IncrementalUpdateTask
from core.apps.query.models import QueryTask
from core.apps.tasks.models import WorkflowTaskInstance, utc_now
from core.apps.tasks.schemas import TaskAction
from core.apps.users.models import User
from config import DolphinSchedulerSettings
from core.database.session import database_engine, database_session_factory
from core.scheduler.client import DolphinSchedulerClient, StreamedLog
from core.scheduler.domain import FAILURE_STATES, TERMINAL_STATES
from core.scheduler.errors import DolphinSchedulerError
from core.scheduler.workflows import ensure_incremental_workflow_definition, ensure_workflow_definition

LOGGER = logging.getLogger(__name__)
SCHEDULER_TIMEZONE = ZoneInfo("Asia/Shanghai")
POLLER_LOCK_ID = 280284398913
BACKFILL_RETRY_INTERVAL = timedelta(hours=1)
APPLICATION_MODELS = (
    ("query", QueryTask),
    ("factor", FactorTask),
    ("backtest", BacktestTask),
    ("incremental", IncrementalUpdateTask),
)
PROCESS_ACTIONS = {
    TaskAction.STOP: "STOP",
    TaskAction.PAUSE: "PAUSE",
    TaskAction.RESUME: "RECOVER_SUSPENDED_PROCESS",
    TaskAction.RERUN: "REPEAT_RUNNING",
    TaskAction.RETRY_FAILED: "START_FAILURE_TASK_PROCESS",
}


class TaskExecutionService:
    def __init__(
        self,
        application: str,
        model: type[Any],
        submission_attempts: int = 40,
        submission_interval: float = 0.25,
    ) -> None:
        self.application = application
        self.model = model
        self.submission_attempts = submission_attempts
        self.submission_interval = submission_interval

    def submit(self, session: Session, user_id: int, payload: dict[str, Any], outputs: list[str]) -> Any:
        created_at = utc_now()
        task = self.model(
            user_id=user_id,
            payload=payload,
            requested_outputs=outputs,
            state="CREATED",
            task_id_history=[],
            process_instance_history=[],
            state_history=[{"state": "CREATED", "timestamp": created_at.isoformat()}],
            events=[],
        )
        session.add(task)
        session.flush()
        return self.submit_record(session, task, payload, outputs, create_directory=True)

    def resubmit(self, session: Session, task: Any, payload: dict[str, Any], outputs: list[str]) -> Any:
        if task.state not in TERMINAL_STATES:
            raise RuntimeError(f"{task.state} 状态不能覆盖分析")
        task.task_id_history = append_unique(task.task_id_history, task.task_id) if task.task_id is not None else list(task.task_id_history or [])
        task.process_instance_history = append_unique(task.process_instance_history, task.process_instance_id) if task.process_instance_id is not None else list(task.process_instance_history or [])
        record_event(task, "ANALYSIS_REPLACED", task_id=task.task_id, process_instance_id=task.process_instance_id)
        task.task_id = None
        task.process_instance_id = None
        task.process_state = None
        task.workflow_tasks = []
        task.host = None
        task.retry_times = None
        task.max_retry_times = None
        task.started_at = None
        task.finished_at = None
        task.duration_seconds = None
        task.last_synced_at = None
        task.error = None
        task.payload = payload
        task.requested_outputs = outputs
        record_state(task, "CREATED")
        session.commit()
        return self.submit_record(session, task, payload, outputs, create_directory=False)

    def submit_record(self, session: Session, task: Any, payload: dict[str, Any], outputs: list[str], *, create_directory: bool) -> Any:
        record_id = task.id
        task_dir = DolphinSchedulerSettings.SHARED_DIR / self.application / str(record_id) if create_directory else resolve_task_directory(self.application, task)
        task.output_dir = str(task_dir / "output")
        task.input_file = str(task_dir / "input.json")
        task.payload = payload
        task.requested_outputs = outputs
        last_error: DolphinSchedulerError | None = None
        try:
            if create_directory:
                Path(task.output_dir).mkdir(parents=True, exist_ok=False)
            else:
                output_dir = Path(task.output_dir)
                if output_dir.exists():
                    shutil.rmtree(output_dir)
                output_dir.mkdir(parents=False)
            temporary = Path(task.input_file).with_suffix(".json.tmp")
            temporary.write_text(json.dumps({**payload, "output_dir": "output"}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(task.input_file)
            project_code, definition = ensure_workflow_definition(self.application)
            task.project_code = project_code
            task.process_definition_code = int(definition["code"])
            task.workflow_name = str(definition["name"])
            record_state(task, "SUBMITTING")
            session.commit()
            with DolphinSchedulerClient() as client:
                client.start_process_instance(
                    project_code=project_code,
                    process_definition_code=task.process_definition_code,
                    start_params={"input_file": task.input_file, "job_id": f"{self.application}:{record_id}", "output": " ".join(outputs)},
                )
                record_state(task, "SUBMITTED")
                task.error = None
                session.commit()
                for attempt in range(self.submission_attempts):
                    try:
                        self.synchronize(session, task, client=client)
                    except DolphinSchedulerError as error:
                        last_error = error
                    if task.task_id is not None:
                        return task
                    if attempt + 1 < self.submission_attempts:
                        time.sleep(self.submission_interval)
        except (DolphinSchedulerError, OSError, ValueError) as error:
            session.rollback()
            task = session.get(self.model, record_id)
            if task is not None:
                if task.state in {"CREATED", "SUBMITTING"}:
                    record_state(task, "SUBMIT_FAILED")
                task.error = str(error)
                task.last_synced_at = utc_now()
                session.commit()
            raise
        detail = f": {last_error}" if last_error is not None else ""
        message = f"任务已提交，但 DolphinScheduler 未在 {self.submission_attempts * self.submission_interval:g} 秒内创建 task instance{detail}"
        task.error = message
        task.last_synced_at = utc_now()
        session.commit()
        raise DolphinSchedulerError(message)

    def synchronize(self, session: Session, task: Any, client: DolphinSchedulerClient | None = None) -> Any:
        if not task.project_code or not task.process_definition_code:
            return task
        if client is None:
            with DolphinSchedulerClient() as active_client:
                return self.synchronize(session, task, client=active_client)
        try:
            instance = self.locate_process_instance(client, task)
            if instance is not None:
                self.apply_process_state(task, instance)
                instances = client.process_instance_tasks(project_code=int(task.project_code), process_instance_id=task.process_instance_id)
                synchronize_workflow_task_instances(
                    session,
                    application=self.application,
                    record_id=task.id,
                    instances=instances,
                )
                definition = client.process_definition_details(
                    int(task.project_code),
                    int(task.process_definition_code),
                )
                task.workflow_tasks = workflow_task_information(instances, definition)
                self.synchronize_task_instances(client, task, instance, instances)
            task.error = None if task.state not in FAILURE_STATES else task.error
            task.last_synced_at = utc_now()
            session.commit()
            return task
        except DolphinSchedulerError as error:
            task.error = str(error)
            task.last_synced_at = utc_now()
            session.commit()
            raise

    def synchronize_task_instances(
        self,
        client: DolphinSchedulerClient,
        task: Any,
        process_instance: dict[str, Any],
        instances: list[dict[str, Any]],
    ) -> None:
        task_id_history = set(task.task_id_history or [])
        runtime_tasks = [
            item
            for item in instances
            if item.get("name") == self.application
            and int(item["id"]) not in task_id_history
        ]
        runtime_task = max(
            runtime_tasks,
            key=lambda item: int(item["id"]),
            default=None,
        )
        if runtime_task is not None:
            self.apply_task_state(client, task, runtime_task)

    def locate_process_instance(self, client: DolphinSchedulerClient, task: Any) -> dict[str, Any] | None:
        if task.process_instance_id is not None:
            return client.process_instance(int(task.project_code), task.process_instance_id)
        marker = f"{self.application}:{task.id}"
        process_instance_history = set(task.process_instance_history or [])
        for page_no in range(1, 21):
            instances = client.process_instances(
                project_code=int(task.project_code),
                process_definition_code=int(task.process_definition_code),
                page_no=page_no,
                page_size=100,
            )
            for instance in sorted(instances, key=lambda item: int(item.get("id", 0)), reverse=True):
                if int(instance.get("id", 0)) not in process_instance_history and process_parameter(instance, "job_id") == marker:
                    return instance
            if len(instances) < 100:
                break
        return None

    def apply_process_state(self, task: Any, instance: dict[str, Any]) -> None:
        process_instance_id = int(instance["id"])
        if task.process_instance_id is not None and task.process_instance_id != process_instance_id:
            task.process_instance_history = append_unique(task.process_instance_history, task.process_instance_id)
        task.process_instance_id = process_instance_id
        task.process_state = str(instance.get("state") or task.process_state or "") or None

    def apply_task_state(self, client: DolphinSchedulerClient, task: Any, runtime_task: dict[str, Any]) -> None:
        task_id = int(runtime_task["id"])
        if task.task_id is not None and task.task_id != task_id:
            task.task_id_history = append_unique(task.task_id_history, task.task_id)
        task.task_id = task_id
        previous_state = task.state
        state = str(runtime_task.get("state") or task.state)
        record_state(task, state)
        task.host = runtime_task.get("host")
        task.retry_times = integer_or_none(runtime_task.get("retryTimes"))
        task.max_retry_times = integer_or_none(runtime_task.get("maxRetryTimes"))
        task.started_at = parse_scheduler_datetime(runtime_task.get("startTime"))
        task.finished_at = parse_scheduler_datetime(runtime_task.get("endTime"))
        task.duration_seconds = duration_seconds(task.started_at, task.finished_at)
        if state in FAILURE_STATES:
            if previous_state != state or not task.error:
                task.error = failure_message(client, task_id, state)
        else:
            task.error = None


class IncrementalUpdateExecutionService(TaskExecutionService):
    """Submit and track one multi-task incremental-update workflow."""

    def submit_workflow(self, session: Session, user_id: int) -> dict[str, Any]:
        created_at = utc_now()
        task = self.model(
            user_id=user_id,
            payload={},
            requested_outputs=[],
            state="CREATED",
            task_id_history=[],
            process_instance_history=[],
            workflow_tasks=[],
            state_history=[{"state": "CREATED", "timestamp": created_at.isoformat()}],
            events=[],
        )
        session.add(task)
        session.flush()
        record_id = task.id
        job_id = f"incremental:{record_id}"
        task.payload = {"job_id": job_id}
        try:
            project_code, definition = ensure_incremental_workflow_definition()
            task.project_code = project_code
            task.process_definition_code = int(definition["code"])
            task.workflow_name = str(definition["name"])
            record_state(task, "SUBMITTING")
            session.commit()
            with DolphinSchedulerClient() as client:
                definition_details = client.process_definition_details(
                    int(task.project_code),
                    int(task.process_definition_code),
                )
                task.workflow_tasks = workflow_task_information([], definition_details)
                session.commit()
                submission = client.start_process_instance(
                    project_code=project_code,
                    process_definition_code=task.process_definition_code,
                    start_params={"job_id": job_id},
                    failure_strategy="CONTINUE",
                )
                record_state(task, "SUBMITTED")
                record_event(task, "WORKFLOW_SUBMITTED", job_id=job_id)
                session.commit()
                for attempt in range(self.submission_attempts):
                    try:
                        self.synchronize(session, task, client=client)
                    except DolphinSchedulerError:
                        pass
                    if task.process_instance_id is not None:
                        break
                    if attempt + 1 < self.submission_attempts:
                        time.sleep(self.submission_interval)
            return {
                "task": task,
                "job_id": job_id,
                "scheduler_submission": submission,
            }
        except (DolphinSchedulerError, ValueError) as error:
            session.rollback()
            task = session.get(self.model, record_id)
            if task is not None:
                if task.state in {"CREATED", "SUBMITTING"}:
                    record_state(task, "SUBMIT_FAILED")
                task.error = str(error)
                task.last_synced_at = utc_now()
                session.commit()
            raise

    def synchronize_task_instances(
        self,
        client: DolphinSchedulerClient,
        task: Any,
        process_instance: dict[str, Any],
        instances: list[dict[str, Any]],
    ) -> None:
        representative = next(
            (
                item
                for item in instances
                if task.task_id is not None and int(item["id"]) == task.task_id
            ),
            None,
        )
        if representative is None:
            representative = min(
                instances,
                key=lambda item: int(item["id"]),
                default=None,
            )
        if representative is not None:
            representative_id = int(representative["id"])
            if task.task_id is not None and task.task_id != representative_id:
                task.task_id_history = append_unique(task.task_id_history, task.task_id)
            task.task_id = representative_id

        hosts = sorted({str(item["host"]) for item in instances if item.get("host")})
        task.host = ", ".join(hosts) or None
        retry_times = [integer_or_none(item.get("retryTimes")) for item in instances]
        max_retry_times = [integer_or_none(item.get("maxRetryTimes")) for item in instances]
        task.retry_times = max((value for value in retry_times if value is not None), default=None)
        task.max_retry_times = max((value for value in max_retry_times if value is not None), default=None)
        task.started_at = parse_scheduler_datetime(process_instance.get("startTime"))
        task.finished_at = parse_scheduler_datetime(process_instance.get("endTime"))
        task.duration_seconds = duration_seconds(task.started_at, task.finished_at)

        state = str(process_instance.get("state") or task.process_state or task.state)
        previous_state = task.state
        record_state(task, state)
        if state in FAILURE_STATES:
            failed_tasks = [item for item in instances if item.get("state") in FAILURE_STATES]
            failed_task = max(
                failed_tasks,
                key=lambda item: int(item["id"]),
                default=None,
            )
            if failed_task is not None and (previous_state != state or not task.error):
                task.error = failure_message(client, int(failed_task["id"]), state)
        else:
            task.error = None


class TaskGatewayService:
    def __init__(self) -> None:
        self.executors = {
            application: TaskExecutionService(application, model)
            for application, model in APPLICATION_MODELS
        }
        self.executors["incremental"] = IncrementalUpdateExecutionService(
            "incremental",
            IncrementalUpdateTask,
        )

    def submit_incremental(self, session: Session, user_id: int) -> dict[str, Any]:
        executor = self.executors["incremental"]
        if not isinstance(executor, IncrementalUpdateExecutionService):
            raise RuntimeError("增量更新任务执行器配置错误")
        return executor.submit_workflow(session, user_id)

    def status(self, session: Session, user: User, task_id: int) -> dict[str, Any]:
        application, task = self.find_accessible_task(session, user, task_id)
        task = self.executors[application].synchronize(session, task)
        return task_status(application, task, task_id)

    def list(self, session: Session, user: User, page: int, page_size: int, application: str | None, state: str | None) -> dict[str, Any]:
        selected_models = ((name, model) for name, model in APPLICATION_MODELS if application is None or name == application)
        candidates: list[tuple[str, Any]] = []
        total = 0
        window_size = page * page_size
        for name, model in selected_models:
            conditions = [] if user.is_admin else [model.user_id == user.id]
            if state == "active":
                conditions.append(model.state.not_in(TERMINAL_STATES))
            elif state == "success":
                conditions.append(model.state.in_(("SUCCESS", "FORCED_SUCCESS")))
            elif state == "failure":
                conditions.append(model.state.in_((*FAILURE_STATES, "SUBMIT_FAILED")))
            total += int(session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)
            tasks = session.scalars(select(model).where(*conditions).order_by(model.created_at.desc(), model.id.desc()).limit(window_size))
            candidates.extend((name, task) for task in tasks)
        candidates.sort(key=lambda item: (item[1].created_at, item[1].id), reverse=True)
        offset = (page - 1) * page_size
        page_candidates = candidates[offset:offset + page_size]
        owner_ids = {task.user_id for _, task in page_candidates}
        owner_names = {
            owner.id: owner.username
            for owner in session.scalars(select(User).where(User.id.in_(owner_ids)))
        }
        items = [
            task_list_item(name, task, owner_names.get(task.user_id, f"用户 #{task.user_id}"))
            for name, task in page_candidates
        ]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def log(self, session: Session, user: User, task_id: int, skip_line_num: int, limit: int) -> dict[str, Any]:
        application, task = self.find_accessible_task(session, user, task_id)
        with DolphinSchedulerClient() as client:
            page = client.task_log(task_instance_id=task_id, skip_line_num=skip_line_num, limit=limit)
        workflow_task = next((item for item in task.workflow_tasks or [] if integer_or_none(item.get("task_id")) == task_id), None)
        state = str(workflow_task.get("state")) if workflow_task is not None else task.state if task.task_id == task_id else "HISTORICAL"
        return {"task_id": task_id, "state": state, **page}

    def stream_log(self, session: Session, user: User, task_id: int) -> StreamedLog:
        application, task = self.find_accessible_task(session, user, task_id)
        client = DolphinSchedulerClient()
        try:
            client.login()
            return client.stream_task_log(project_code=int(task.project_code), task_instance_id=task_id)
        except Exception:
            client.session.close()
            raise

    def control(self, session: Session, user: User, task_id: int, action: TaskAction) -> dict[str, Any]:
        application, task = self.find_accessible_task(session, user, task_id)
        if task.task_id != task_id:
            raise RuntimeError("历史 task instance 不能执行控制操作")
        if application == "factor" and getattr(task, "saved", False) and action in {TaskAction.RERUN, TaskAction.RETRY_FAILED}:
            raise RuntimeError("已保存版本的分析任务不能重跑")
        validate_action(task, action)
        with DolphinSchedulerClient() as client:
            if action is TaskAction.FORCE_SUCCESS:
                submission = client.execute_task_instance(int(task.project_code), task_id, action.value)
            else:
                submission = client.execute_process_instance(int(task.project_code), int(task.process_instance_id), PROCESS_ACTIONS[action])
        record_event(task, "CONTROL_REQUESTED", action=action.value, task_id=task_id, process_instance_id=task.process_instance_id)
        if action in {TaskAction.RERUN, TaskAction.RETRY_FAILED}:
            task.task_id_history = append_unique(task.task_id_history, task_id)
            task.task_id = None
            task.workflow_tasks = []
            task.started_at = None
            task.finished_at = None
            task.duration_seconds = None
            task.error = None
            record_state(task, "SUBMITTED")
        session.commit()
        return {"action": action, "scheduler_submission": submission, "task": task_status(application, task, task_id)}

    def delete(self, session: Session, user: User, task_id: int) -> dict[str, Any]:
        application, task = self.find_accessible_task(session, user, task_id)
        if task.state not in TERMINAL_STATES:
            raise RuntimeError(f"{task.state} 状态不能删除")
        if application == "factor" and getattr(task, "saved", False):
            raise RuntimeError("已保存版本的分析任务不能单独删除")
        record_id = task.id
        task_dir = None if application == "incremental" else resolve_task_directory(application, task)
        delete_workflow_task_mappings(session, application, [task.id])
        session.delete(task)
        session.commit()
        if task_dir is not None and task_dir.exists():
            shutil.rmtree(task_dir)
        return {"application": application, "record_id": record_id, "task_id": task_id}

    def find_accessible_task(self, session: Session, user: User, task_id: int) -> tuple[str, Any]:
        mapping = session.get(WorkflowTaskInstance, task_id)
        if mapping is not None:
            model = dict(APPLICATION_MODELS).get(mapping.application)
            task = session.get(model, mapping.record_id) if model is not None else None
            if task is None or (not user.is_admin and task.user_id != user.id):
                raise FileNotFoundError(f"任务不存在: {task_id}")
            return mapping.application, task

        for application, model in APPLICATION_MODELS:
            statement = select(model).where(model.task_id == task_id)
            if not user.is_admin:
                statement = statement.where(model.user_id == user.id)
            task = session.scalar(statement)
            if task is not None:
                return application, task
        raise FileNotFoundError(f"任务不存在: {task_id}")

    def poll_once(self) -> int:
        engine = database_engine()
        with engine.connect() as lock_connection:
            if lock_connection.dialect.name == "postgresql":
                acquired = bool(lock_connection.scalar(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": POLLER_LOCK_ID}))
                if not acquired:
                    return 0
            try:
                return self.poll_records()
            finally:
                if lock_connection.dialect.name == "postgresql":
                    lock_connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": POLLER_LOCK_ID})

    def poll_records(self) -> int:
        synchronized = 0
        backfill_retry_before = utc_now() - BACKFILL_RETRY_INTERVAL
        with database_session_factory()() as session, DolphinSchedulerClient() as client:
            for application, model in APPLICATION_MODELS:
                active_statement = select(model).where(model.state.not_in(TERMINAL_STATES)).order_by(model.id).limit(DolphinSchedulerSettings.POLL_BATCH_SIZE)
                backfill_statement = workflow_task_backfill_statement(
                    model,
                    retry_before=backfill_retry_before,
                    limit=DolphinSchedulerSettings.POLL_BATCH_SIZE,
                )
                records = [*session.scalars(active_statement), *session.scalars(backfill_statement)]
                for task in records:
                    try:
                        self.executors[application].synchronize(session, task, client=client)
                        synchronized += 1
                    except DolphinSchedulerError:
                        continue
                    except Exception as error:
                        task_record_id = task.id
                        session.rollback()
                        failed_task = session.get(model, task_record_id)
                        if failed_task is not None:
                            failed_task.error = str(error)
                            failed_task.last_synced_at = utc_now()
                            session.commit()
                        LOGGER.exception("同步 %s task %s 失败: %s", application, task_record_id, error)
        return synchronized


async def poll_task_statuses(stop_event: asyncio.Event) -> None:
    service = TaskGatewayService()
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(service.poll_once)
        except Exception:
            LOGGER.exception("DolphinScheduler 后台状态轮询失败")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=DolphinSchedulerSettings.POLL_INTERVAL_SECONDS)
        except TimeoutError:
            continue


def task_information(application: str, task: Any) -> dict[str, Any]:
    return {
        "application": application,
        "record_id": task.id,
        "user_id": task.user_id,
        "task_id": task.task_id,
        "task_id_history": task.task_id_history or [],
        "process_instance_id": task.process_instance_id,
        "process_instance_history": task.process_instance_history or [],
        "workflow_tasks": task.workflow_tasks or [],
        "project_code": task.project_code,
        "process_definition_code": task.process_definition_code,
        "workflow_name": task.workflow_name,
        "process_state": task.process_state,
        "state": task.state,
        "error": task.error,
        "host": task.host,
        "retry_times": task.retry_times,
        "max_retry_times": task.max_retry_times,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "duration_seconds": task.duration_seconds,
        "last_synced_at": task.last_synced_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "state_history": task.state_history or [],
        "events": task.events or [],
    }


def workflow_task_backfill_statement(
    model: type[Any],
    *,
    retry_before: datetime,
    limit: int,
) -> Any:
    """Select an old-task backfill batch without retrying recent failures."""
    return select(model).where(
        model.state.in_(TERMINAL_STATES),
        model.process_instance_id.is_not(None),
        model.workflow_tasks == [],
        or_(
            model.last_synced_at.is_(None),
            model.last_synced_at < retry_before,
        ),
    ).order_by(
        model.last_synced_at.asc().nullsfirst(),
        model.id,
    ).limit(limit)


def task_status(application: str, task: Any, requested_task_id: int) -> dict[str, Any]:
    return {**task_information(application, task), "requested_task_id": requested_task_id}


def task_list_item(application: str, task: Any, owner_username: str) -> dict[str, Any]:
    return {
        **task_information(application, task),
        "owner_username": owner_username,
        "payload": task.payload,
        "requested_outputs": task.requested_outputs or [],
    }


def delete_workflow_task_mappings(
    session: Session,
    application: str,
    record_ids: list[int],
) -> None:
    """Delete indexed child-task mappings before their parent records."""
    if not record_ids:
        return
    session.execute(
        delete(WorkflowTaskInstance).where(
            WorkflowTaskInstance.application == application,
            WorkflowTaskInstance.record_id.in_(record_ids),
        )
    )


def synchronize_workflow_task_instances(
    session: Session,
    *,
    application: str,
    record_id: int,
    instances: list[dict[str, Any]],
) -> None:
    """Persist indexed task-instance ownership without discarding old attempts."""
    observed = {
        task_instance_id: integer_or_none(instance.get("taskCode"))
        for instance in instances
        if (task_instance_id := integer_or_none(instance.get("id"))) is not None
    }
    if not observed:
        return

    existing = {
        mapping.task_instance_id: mapping
        for mapping in session.scalars(
            select(WorkflowTaskInstance).where(
                WorkflowTaskInstance.task_instance_id.in_(observed)
            )
        )
    }
    for task_instance_id, task_code in observed.items():
        mapping = existing.get(task_instance_id)
        if mapping is not None:
            if (
                mapping.application != application
                or mapping.record_id != record_id
            ):
                raise RuntimeError(
                    "DolphinScheduler task instance 映射冲突: "
                    f"{task_instance_id}"
                )
            if mapping.task_code is None and task_code is not None:
                mapping.task_code = task_code
            continue
        session.add(
            WorkflowTaskInstance(
                task_instance_id=task_instance_id,
                application=application,
                record_id=record_id,
                task_code=task_code,
            )
        )


def workflow_task_information(
    instances: list[dict[str, Any]],
    definition: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    latest_instances: dict[int, dict[str, Any]] = {}
    for instance in instances:
        task_code = integer_or_none(instance.get("taskCode"))
        task_id = integer_or_none(instance.get("id"))
        if task_code is None or task_id is None:
            continue
        current = latest_instances.get(task_code)
        if current is None or task_id > int(current.get("id") or 0):
            latest_instances[task_code] = instance

    tasks: list[dict[str, Any]] = []
    definition_codes: set[int] = set()
    for node in ordered_workflow_definition_tasks(definition or {}):
        task_code = int(node["code"])
        definition_codes.add(task_code)
        instance = latest_instances.get(task_code)
        task_type = (instance or {}).get("taskType") or node.get("taskType") or "UNKNOWN"
        tasks.append(
            {
                "task_code": task_code,
                "task_id": integer_or_none(instance.get("id")) if instance else None,
                "name": str(node.get("name") or f"Task {task_code}"),
                "task_type": str(task_type),
                "state": str(instance.get("state") or "WAITING") if instance else "WAITING",
            }
        )

    for task_code, instance in sorted(latest_instances.items(), key=lambda item: int(item[1].get("id") or 0)):
        if task_code in definition_codes:
            continue
        task_id = int(instance["id"])
        tasks.append(
            {
                "task_code": task_code,
                "task_id": task_id,
                "name": str(instance.get("name") or f"Task {task_id}"),
                "task_type": str(instance.get("taskType") or "UNKNOWN"),
                "state": str(instance.get("state") or "UNKNOWN"),
            }
        )
    return tasks


def ordered_workflow_definition_tasks(definition: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [
        node
        for node in definition.get("taskDefinitionList") or []
        if integer_or_none(node.get("code")) is not None
    ]
    nodes_by_code = {int(node["code"]): node for node in nodes}
    dependencies = {code: set() for code in nodes_by_code}
    for relation in definition.get("processTaskRelationList") or []:
        pre_task_code = integer_or_none(relation.get("preTaskCode"))
        post_task_code = integer_or_none(relation.get("postTaskCode"))
        if pre_task_code in nodes_by_code and post_task_code in nodes_by_code:
            dependencies[post_task_code].add(pre_task_code)

    ordered: list[dict[str, Any]] = []
    emitted: set[int] = set()
    pending = [int(node["code"]) for node in nodes]
    while pending:
        ready = [code for code in pending if dependencies[code] <= emitted]
        if not ready:
            ready = list(pending)
        for code in ready:
            ordered.append(nodes_by_code[code])
            emitted.add(code)
            pending.remove(code)
    return ordered


def validate_action(task: Any, action: TaskAction) -> None:
    if task.process_instance_id is None:
        raise RuntimeError("DolphinScheduler 尚未创建 process instance")
    if action in {TaskAction.STOP, TaskAction.FORCE_SUCCESS} and task.task_id is None:
        raise RuntimeError("DolphinScheduler 尚未创建 task instance")
    if action is TaskAction.STOP and task.state in TERMINAL_STATES:
        raise RuntimeError(f"{task.state} 状态不能停止")
    if action is TaskAction.FORCE_SUCCESS and task.state == "SUCCESS":
        raise RuntimeError("SUCCESS 状态不需要强制成功")
    if action is TaskAction.PAUSE and task.process_state in TERMINAL_STATES:
        raise RuntimeError(f"{task.process_state} 状态不能暂停")
    if action is TaskAction.RESUME and task.process_state != "PAUSE":
        raise RuntimeError(f"{task.process_state} 状态不能恢复")
    if action is TaskAction.RERUN and task.process_state not in TERMINAL_STATES:
        raise RuntimeError(f"{task.process_state} 状态不能重跑")
    if action is TaskAction.RETRY_FAILED and task.process_state != "FAILURE":
        raise RuntimeError(f"{task.process_state} 状态不能从失败节点续跑")


def process_parameter(instance: dict[str, Any], name: str) -> str | None:
    global_params = instance.get("globalParams")
    if isinstance(global_params, str):
        try:
            global_params = json.loads(global_params)
        except json.JSONDecodeError:
            global_params = []
    if isinstance(global_params, list):
        for parameter in global_params:
            if isinstance(parameter, dict) and parameter.get("prop") == name:
                value = parameter.get("value")
                return str(value) if value is not None else None
    command_param = instance.get("commandParam")
    if isinstance(command_param, str):
        try:
            command_param = json.loads(command_param)
        except json.JSONDecodeError:
            return None
    if isinstance(command_param, dict) and isinstance(command_param.get("StartParams"), str):
        try:
            value = json.loads(command_param["StartParams"]).get(name)
            return str(value) if value is not None else None
        except json.JSONDecodeError:
            return None
    return None


def record_state(task: Any, state: str) -> None:
    if task.state == state:
        return
    task.state = state
    task.state_history = [*(task.state_history or []), {"state": state, "timestamp": utc_now().isoformat()}]


def record_event(task: Any, event: str, **details: Any) -> None:
    task.events = [*(task.events or []), {"event": event, "timestamp": utc_now().isoformat(), **details}]


def append_unique(values: list[int] | None, value: int) -> list[int]:
    return [*(values or []), value] if value not in (values or []) else list(values or [])


def integer_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None


def parse_scheduler_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SCHEDULER_TIMEZONE)
    return parsed.astimezone(UTC)


def duration_seconds(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    return round((finished_at - started_at).total_seconds(), 3) if started_at and finished_at else None


def failure_message(client: DolphinSchedulerClient, task_id: int, state: str) -> str:
    skip_line_num = 0
    tail = ""
    pages = 0
    try:
        while pages < 100:
            pages += 1
            page = client.task_log(task_instance_id=task_id, skip_line_num=skip_line_num, limit=1000)
            tail = (tail + page["message"])[-4000:]
            if not page["has_more"] or page["next_line_num"] == skip_line_num:
                break
            skip_line_num = page["next_line_num"]
    except DolphinSchedulerError:
        return f"DolphinScheduler task state: {state}"
    return tail.strip() or f"DolphinScheduler task state: {state}"


def resolve_task_directory(application: str, task: Any) -> Path:
    if not task.input_file:
        raise RuntimeError(f"任务 {task.id} 没有 input_file")
    task_dir = Path(task.input_file).resolve().parent
    expected_parent = (DolphinSchedulerSettings.SHARED_DIR / application).resolve()
    if task_dir.parent != expected_parent or task_dir.name != str(task.id):
        raise RuntimeError(f"拒绝清理非任务目录: {task_dir}")
    return task_dir
