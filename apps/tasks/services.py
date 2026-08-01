"""Task submission, synchronization, authorization, logs, control, and polling."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from apps.backtest.models import BacktestTask
from apps.factor.models import FactorTask
from apps.query.models import QueryTask
from apps.tasks.models import utc_now
from apps.tasks.schemas import TaskAction
from config.database import database_engine, database_session_factory
from config.dolphinscheduler.client import DolphinSchedulerClient, StreamedLog
from config.dolphinscheduler.domain import FAILURE_STATES, TERMINAL_STATES
from config.dolphinscheduler.errors import DolphinSchedulerError
from config.dolphinscheduler.workflows import ensure_workflow_definition
from config.settings import DolphinSchedulerSettings

LOGGER = logging.getLogger(__name__)
SCHEDULER_TIMEZONE = ZoneInfo("Asia/Shanghai")
POLLER_LOCK_ID = 280284398913
APPLICATION_MODELS = (("query", QueryTask), ("factor", FactorTask), ("backtest", BacktestTask))
PROCESS_ACTIONS = {
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
        settings: DolphinSchedulerSettings | None = None,
        client_factory: Any = None,
        workflow_resolver: Any = None,
        submission_attempts: int = 40,
        submission_interval: float = 0.25,
    ) -> None:
        self.application = application
        self.model = model
        self.settings = settings or DolphinSchedulerSettings.from_environment()
        self.client_factory = client_factory or (lambda: DolphinSchedulerClient(self.settings))
        self.workflow_resolver = workflow_resolver or ensure_workflow_definition
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
        record_id = task.id
        task_dir = self.settings.shared_dir / self.application / str(record_id)
        task.output_dir = str(task_dir / "output")
        task.input_file = str(task_dir / "input.json")
        last_error: DolphinSchedulerError | None = None
        try:
            Path(task.output_dir).mkdir(parents=True, exist_ok=False)
            temporary = Path(task.input_file).with_suffix(".json.tmp")
            temporary.write_text(json.dumps({**payload, "output_dir": "output"}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(task.input_file)
            project_code, definition = self.workflow_resolver(self.application, self.settings)
            task.project_code = project_code
            task.process_definition_code = int(definition["code"])
            task.workflow_name = str(definition["name"])
            record_state(task, "SUBMITTING")
            session.commit()
            with self.client_factory() as client:
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
            with self.client_factory() as active_client:
                return self.synchronize(session, task, client=active_client)
        try:
            instance = self.locate_process_instance(client, task)
            if instance is not None:
                self.apply_process_state(task, instance)
                instances = client.process_instance_tasks(project_code=int(task.project_code), process_instance_id=task.process_instance_id)
                task_id_history = set(task.task_id_history or [])
                runtime_tasks = [item for item in instances if item.get("name") == self.application and int(item["id"]) not in task_id_history]
                runtime_task = max(runtime_tasks, key=lambda item: int(item["id"]), default=None)
                if runtime_task is not None:
                    self.apply_task_state(client, task, runtime_task)
            task.error = None if task.state not in FAILURE_STATES else task.error
            task.last_synced_at = utc_now()
            session.commit()
            return task
        except DolphinSchedulerError as error:
            task.error = str(error)
            task.last_synced_at = utc_now()
            session.commit()
            raise

    def locate_process_instance(self, client: DolphinSchedulerClient, task: Any) -> dict[str, Any] | None:
        if task.process_instance_id is not None:
            return client.process_instance(int(task.project_code), task.process_instance_id)
        marker = f"{self.application}:{task.id}"
        for page_no in range(1, 21):
            instances = client.process_instances(
                project_code=int(task.project_code),
                process_definition_code=int(task.process_definition_code),
                page_no=page_no,
                page_size=100,
            )
            for instance in sorted(instances, key=lambda item: int(item.get("id", 0)), reverse=True):
                if process_parameter(instance, "job_id") == marker:
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


class TaskGatewayService:
    def __init__(
        self,
        settings: DolphinSchedulerSettings | None = None,
        client_factory: Any = None,
        engine: Any = None,
        session_factory: Any = None,
    ) -> None:
        self.settings = settings or DolphinSchedulerSettings.from_environment()
        self.client_factory = client_factory or (lambda: DolphinSchedulerClient(self.settings))
        self.engine = engine
        self.session_factory = session_factory
        self.executors = {
            application: TaskExecutionService(application, model, settings=self.settings, client_factory=self.client_factory)
            for application, model in APPLICATION_MODELS
        }

    def status(self, session: Session, user_id: int, task_id: int) -> dict[str, Any]:
        application, task = self.find_owned_task(session, user_id, task_id)
        task = self.executors[application].synchronize(session, task)
        return task_status(application, task, task_id)

    def log(self, session: Session, user_id: int, task_id: int, skip_line_num: int, limit: int) -> dict[str, Any]:
        application, task = self.find_owned_task(session, user_id, task_id)
        with self.client_factory() as client:
            page = client.task_log(task_instance_id=task_id, skip_line_num=skip_line_num, limit=limit)
        state = task.state if task.task_id == task_id else "HISTORICAL"
        return {"task_id": task_id, "state": state, **page}

    def stream_log(self, session: Session, user_id: int, task_id: int) -> StreamedLog:
        application, task = self.find_owned_task(session, user_id, task_id)
        client = self.client_factory()
        try:
            client.login()
            return client.stream_task_log(project_code=int(task.project_code), task_instance_id=task_id)
        except Exception:
            client.session.close()
            raise

    def control(self, session: Session, user_id: int, task_id: int, action: TaskAction) -> dict[str, Any]:
        application, task = self.find_owned_task(session, user_id, task_id)
        if task.task_id != task_id:
            raise RuntimeError("历史 task instance 不能执行控制操作")
        validate_action(task, action)
        with self.client_factory() as client:
            if action in {TaskAction.STOP, TaskAction.FORCE_SUCCESS}:
                submission = client.execute_task_instance(int(task.project_code), task_id, action.value)
            else:
                submission = client.execute_process_instance(int(task.project_code), int(task.process_instance_id), PROCESS_ACTIONS[action])
        record_event(task, "CONTROL_REQUESTED", action=action.value, task_id=task_id, process_instance_id=task.process_instance_id)
        if action in {TaskAction.RERUN, TaskAction.RETRY_FAILED}:
            task.task_id_history = append_unique(task.task_id_history, task_id)
            task.task_id = None
            task.started_at = None
            task.finished_at = None
            task.duration_seconds = None
            task.error = None
            record_state(task, "SUBMITTED")
        session.commit()
        return {"action": action, "scheduler_submission": submission, "task": task_status(application, task, task_id)}

    def delete(self, session: Session, user_id: int, task_id: int) -> dict[str, Any]:
        application, task = self.find_owned_task(session, user_id, task_id)
        if task.state not in TERMINAL_STATES:
            raise RuntimeError(f"{task.state} 状态不能删除")
        record_id = task.id
        task_dir = Path(task.input_file).resolve().parent
        expected_parent = (self.settings.shared_dir / application).resolve()
        if task_dir.parent != expected_parent or task_dir.name != str(record_id):
            raise RuntimeError(f"拒绝清理非任务目录: {task_dir}")
        session.delete(task)
        session.commit()
        if task_dir.exists():
            shutil.rmtree(task_dir)
        return {"application": application, "record_id": record_id, "task_id": task_id}

    def find_owned_task(self, session: Session, user_id: int, task_id: int) -> tuple[str, Any]:
        for application, model in APPLICATION_MODELS:
            task = session.scalar(select(model).where(model.user_id == user_id, model.task_id == task_id))
            if task is not None:
                return application, task
        for application, model in APPLICATION_MODELS:
            for task in session.scalars(select(model).where(model.user_id == user_id)):
                if task_id in (task.task_id_history or []):
                    return application, task
        raise FileNotFoundError(f"任务不存在: {task_id}")

    def poll_once(self) -> int:
        engine = self.engine or database_engine()
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
        session_factory = self.session_factory or database_session_factory()
        with session_factory() as session, self.client_factory() as client:
            for application, model in APPLICATION_MODELS:
                statement = select(model).where(model.state.not_in(TERMINAL_STATES)).order_by(model.id).limit(self.settings.poll_batch_size)
                for task in session.scalars(statement):
                    try:
                        self.executors[application].synchronize(session, task, client=client)
                        synchronized += 1
                    except DolphinSchedulerError:
                        continue
                    except Exception as error:
                        task_record_id = task.id
                        session.rollback()
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
            await asyncio.wait_for(stop_event.wait(), timeout=service.settings.poll_interval_seconds)
        except TimeoutError:
            continue


def task_status(application: str, task: Any, requested_task_id: int) -> dict[str, Any]:
    return {
        "application": application,
        "record_id": task.id,
        "requested_task_id": requested_task_id,
        "task_id": task.task_id,
        "task_id_history": task.task_id_history or [],
        "process_instance_id": task.process_instance_id,
        "process_instance_history": task.process_instance_history or [],
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
