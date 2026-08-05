"""Workflow submission, synchronization, querying, control, and polling."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from config import ArenaSettings, DolphinSchedulerSettings
from core.apps.backtest.models import BacktestWorkflowRun
from core.apps.factor.models import FactorWorkflowRun
from core.apps.incremental.models import IncrementalWorkflowRun
from core.apps.query.models import QueryWorkflowRun
from core.apps.users.models import User
from core.apps.workflows.models import WorkflowInstance, WorkflowRun, utc_now
from core.apps.workflows.schemas import WorkflowAction
from core.database.session import database_engine, database_session_factory
from core.scheduler.client import DolphinSchedulerClient
from core.scheduler.domain import (
    APPLICATION_START_PARAMETERS,
    FAILURE_STATES,
    INCREMENTAL_START_PARAMETERS,
    TERMINAL_STATES,
    validate_start_parameters,
)
from core.scheduler.errors import DolphinSchedulerError
from core.scheduler.incremental import (
    normalize_incremental_channel,
    normalize_incremental_workers,
)
from core.scheduler.workflows import (
    ensure_incremental_workflow_definition,
    ensure_workflow_definition,
)
from core.utils.results import (
    cloud_output_location,
    delete_result_objects,
    is_cloud_output,
)

LOGGER = logging.getLogger(__name__)
SCHEDULER_TIMEZONE = ZoneInfo("Asia/Shanghai")
POLLER_LOCK_ID = 280284398913
SUBMISSION_ACTIVE_STATES = frozenset({"CREATED", "SUBMITTING", "SUBMITTED"})
RUN_MODELS = {
    "query": QueryWorkflowRun,
    "factor": FactorWorkflowRun,
    "backtest": BacktestWorkflowRun,
    "incremental": IncrementalWorkflowRun,
}
PROCESS_ACTIONS = {
    WorkflowAction.STOP: "STOP",
    WorkflowAction.PAUSE: "PAUSE",
    WorkflowAction.RESUME: "RECOVER_SUSPENDED_PROCESS",
    WorkflowAction.RERUN: "REPEAT_RUNNING",
    WorkflowAction.RETRY_FAILED: "START_FAILURE_TASK_PROCESS",
}


def workflow_input_json(run: WorkflowRun) -> dict[str, Any]:
    """返回实际写入应用 input.json 的参数。"""
    value = run.payload.get("input_json")
    if not isinstance(value, dict):
        raise RuntimeError(f"{run.application} 工作流没有 input_json")
    return value


def workflow_start_parameters(run: WorkflowRun) -> dict[str, str]:
    """返回实际提交给调度器的工作流启动参数。"""
    value = run.payload.get("start_parameters")
    if not isinstance(value, dict) or any(
        not isinstance(name, str) or not isinstance(item, str)
        for name, item in value.items()
    ):
        raise RuntimeError(f"{run.application} 工作流没有有效的 start_parameters")
    return value


class WorkflowExecutionService:
    def __init__(
        self,
        application: str,
        model: type[WorkflowRun],
        submission_attempts: int = 40,
        submission_interval: float = 0.25,
    ) -> None:
        self.application = application
        self.model = model
        self.submission_attempts = submission_attempts
        self.submission_interval = submission_interval

    def submit(
        self,
        session: Session,
        user_id: int,
        payload: dict[str, Any],
        outputs: list[str],
    ) -> WorkflowRun:
        run = self.model(
            user_id=user_id,
            application=self.application,
            payload={
                "start_parameters": {},
                "input_json": payload,
            },
            requested_outputs=outputs,
            submission_state="CREATED",
            events=[],
        )
        session.add(run)
        session.flush()
        return self.submit_run(session, run, create_directory=True)

    def submit_run(
        self,
        session: Session,
        run: WorkflowRun,
        *,
        create_directory: bool,
    ) -> WorkflowRun:
        run_id = run.id
        try:
            if create_directory:
                workspace_key = uuid4().hex
                run_directory = ArenaSettings.SHARED_DIR / self.application / workspace_key
                if ArenaSettings.SHARED_CLOUD:
                    output_argument, run.output_dir = cloud_output_location(
                        self.application,
                        workspace_key,
                    )
                else:
                    run.output_dir = str(run_directory / "output")
                    output_argument = run.output_dir
                run.input_file = str(run_directory / "input.json")
            else:
                run_directory = None
                output_argument = None

            if run_directory is not None:
                if is_cloud_output(run.output_dir):
                    run_directory.mkdir(parents=True, exist_ok=False)
                else:
                    Path(run.output_dir or "").mkdir(parents=True, exist_ok=False)
                temporary = Path(run.input_file or "").with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(
                        workflow_input_json(run),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                temporary.replace(run.input_file or "")

            if not run.input_file or not output_argument:
                raise RuntimeError(
                    f"{self.application} 工作流缺少运行所需的输入或输出路径"
                )

            project_code, definition = ensure_workflow_definition(self.application)
            run.project_code = project_code
            run.workflow_definition_code = int(definition["code"])
            run.workflow_name = str(definition["name"])
            start_parameters = validate_start_parameters(
                {
                    "input_file": run.input_file,
                    "output_dir": output_argument,
                    "job_id": workflow_marker(run),
                    "output": " ".join(run.requested_outputs),
                    "cloud": str(
                        is_cloud_output(run.output_dir)
                    ).lower(),
                },
                APPLICATION_START_PARAMETERS,
            )
            run.payload = {
                "start_parameters": start_parameters,
                "input_json": workflow_input_json(run),
            }
            set_submission_state(run, "SUBMITTING")
            session.commit()

            with DolphinSchedulerClient() as client:
                client.start_process_instance(
                    project_code=project_code,
                    process_definition_code=run.workflow_definition_code,
                    start_params=start_parameters,
                )
                set_submission_state(run, "SUBMITTED")
                run.error = None
                session.commit()
                self.wait_for_workflow_instance(session, run, client)
            return run
        except (DolphinSchedulerError, OSError, ValueError) as error:
            session.rollback()
            failed_run = session.get(WorkflowRun, run_id)
            if failed_run is not None:
                if failed_run.submission_state in {"CREATED", "SUBMITTING"}:
                    set_submission_state(failed_run, "SUBMIT_FAILED")
                failed_run.error = str(error)
                session.commit()
            raise

    def wait_for_workflow_instance(
        self,
        session: Session,
        run: WorkflowRun,
        client: DolphinSchedulerClient,
    ) -> WorkflowInstance:
        last_error: DolphinSchedulerError | None = None
        for attempt in range(self.submission_attempts):
            try:
                workflow = self.synchronize(session, run, client=client)
                if workflow is not None:
                    return workflow
            except DolphinSchedulerError as error:
                last_error = error
            if attempt + 1 < self.submission_attempts:
                time.sleep(self.submission_interval)
        detail = f": {last_error}" if last_error is not None else ""
        message = (
            "工作流已提交，但 DolphinScheduler 未在 "
            f"{self.submission_attempts * self.submission_interval:g} 秒内创建 workflow instance{detail}"
        )
        run.error = message
        session.commit()
        raise DolphinSchedulerError(message)

    def synchronize(
        self,
        session: Session,
        run: WorkflowRun,
        client: DolphinSchedulerClient | None = None,
    ) -> WorkflowInstance | None:
        if run.project_code is None or run.workflow_definition_code is None:
            return None
        if client is None:
            with DolphinSchedulerClient() as active_client:
                return self.synchronize(session, run, client=active_client)

        workflow = current_workflow_instance(session, run.id)
        scheduler_instance = None
        if workflow is None or run.submission_state in SUBMISSION_ACTIVE_STATES:
            scheduler_instance = self.locate_new_workflow_instance(session, client, run)
            if scheduler_instance is not None:
                workflow_instance_id = int(scheduler_instance["id"])
                if workflow is not None and workflow.workflow_instance_id != workflow_instance_id:
                    workflow.is_current = False
                    session.flush()
                workflow = session.get(WorkflowInstance, workflow_instance_id)
                if workflow is None:
                    workflow = WorkflowInstance(
                        workflow_instance_id=workflow_instance_id,
                        workflow_run_id=run.id,
                        state=str(scheduler_instance.get("state") or "SUBMITTED_SUCCESS"),
                        is_current=True,
                        state_history=[],
                    )
                    session.add(workflow)
                else:
                    workflow.is_current = True
                set_submission_state(run, "WORKFLOW_CREATED")

        if workflow is None:
            return None
        if scheduler_instance is None:
            scheduler_instance = client.process_instance(
                int(run.project_code),
                workflow.workflow_instance_id,
            )
            if (
                run.submission_state in SUBMISSION_ACTIVE_STATES
                and str(scheduler_instance.get("state") or workflow.state) not in TERMINAL_STATES
            ):
                set_submission_state(run, "WORKFLOW_CREATED")

        synchronize_workflow_state(client, run, workflow, scheduler_instance)
        session.commit()
        return workflow

    def locate_new_workflow_instance(
        self,
        session: Session,
        client: DolphinSchedulerClient,
        run: WorkflowRun,
    ) -> dict[str, Any] | None:
        known_ids = set(
            session.scalars(
                select(WorkflowInstance.workflow_instance_id).where(
                    WorkflowInstance.workflow_run_id == run.id
                )
            )
        )
        marker = workflow_marker(run)
        for page_no in range(1, 21):
            instances = client.process_instances(
                project_code=int(run.project_code or 0),
                process_definition_code=int(run.workflow_definition_code or 0),
                page_no=page_no,
                page_size=100,
            )
            for instance in sorted(instances, key=lambda item: int(item.get("id", 0)), reverse=True):
                instance_id = int(instance.get("id", 0))
                if instance_id not in known_ids and process_parameter(instance, "job_id") == marker:
                    return instance
            if len(instances) < 100:
                break
        return None


class IncrementalWorkflowExecutionService(WorkflowExecutionService):
    def submit_incremental(
        self,
        session: Session,
        user_id: int,
        workers: Sequence[str] | None = None,
        channel: str | None = None,
    ) -> tuple[WorkflowRun, Any]:
        selected_workers = normalize_incremental_workers(workers)
        selected_channel = normalize_incremental_channel(channel)
        run = self.model(
            user_id=user_id,
            application="incremental",
            payload={"start_parameters": {}},
            requested_outputs=[],
            submission_state="CREATED",
            events=[],
        )
        session.add(run)
        session.flush()
        run_id = run.id
        submission: Any = None
        try:
            workspace_key = uuid4().hex
            run_directory = (
                ArenaSettings.SHARED_DIR / "incremental" / workspace_key
            )
            output_dir = run_directory / "output"
            output_dir.mkdir(parents=True, exist_ok=False)
            run.output_dir = str(output_dir)
            project_code, definition = ensure_incremental_workflow_definition()
            run.project_code = project_code
            run.workflow_definition_code = int(definition["code"])
            run.workflow_name = str(definition["name"])
            start_parameters = validate_start_parameters(
                {
                    "job_id": workflow_marker(run),
                    "output_dir": str(output_dir),
                    "workers": ",".join(selected_workers),
                    "channel": selected_channel,
                },
                INCREMENTAL_START_PARAMETERS,
            )
            run.payload = {"start_parameters": start_parameters}
            set_submission_state(run, "SUBMITTING")
            session.commit()
            with DolphinSchedulerClient() as client:
                submission = client.start_process_instance(
                    project_code=project_code,
                    process_definition_code=run.workflow_definition_code,
                    start_params=start_parameters,
                    failure_strategy="CONTINUE",
                )
                set_submission_state(run, "SUBMITTED")
                record_event(run, "WORKFLOW_SUBMITTED")
                session.commit()
                self.wait_for_workflow_instance(session, run, client)
            return run, submission
        except (DolphinSchedulerError, ValueError) as error:
            session.rollback()
            failed_run = session.get(WorkflowRun, run_id)
            if failed_run is not None:
                if failed_run.submission_state in {"CREATED", "SUBMITTING"}:
                    set_submission_state(failed_run, "SUBMIT_FAILED")
                failed_run.error = str(error)
                session.commit()
            raise


class WorkflowGatewayService:
    def __init__(self) -> None:
        self.executors: dict[str, WorkflowExecutionService] = {
            application: WorkflowExecutionService(application, model)
            for application, model in RUN_MODELS.items()
        }
        self.executors["incremental"] = IncrementalWorkflowExecutionService(
            "incremental",
            IncrementalWorkflowRun,
        )

    def submit_incremental(
        self,
        session: Session,
        user_id: int,
        workers: Sequence[str] | None = None,
        channel: str | None = None,
    ) -> tuple[WorkflowRun, Any]:
        executor = self.executors["incremental"]
        if not isinstance(executor, IncrementalWorkflowExecutionService):
            raise RuntimeError("增量更新工作流执行器配置错误")
        return executor.submit_incremental(
            session,
            user_id,
            workers,
            channel,
        )

    def status(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
    ) -> dict[str, Any]:
        workflow, run = self.find_accessible_workflow(session, user, workflow_instance_id)
        with DolphinSchedulerClient() as client:
            scheduler_instance = client.process_instance(
                int(run.project_code or 0),
                workflow.workflow_instance_id,
            )
            synchronize_workflow_state(client, run, workflow, scheduler_instance)
            session.commit()
            return workflow_information(client, run, workflow)

    def list(
        self,
        session: Session,
        user: User,
        page: int,
        page_size: int,
        application: str | None,
        state: str | None,
    ) -> dict[str, Any]:
        conditions = [] if user.is_admin else [WorkflowRun.user_id == user.id]
        if application is not None:
            conditions.append(WorkflowRun.application == application)
        if state == "active":
            conditions.append(WorkflowInstance.state.not_in(TERMINAL_STATES))
        elif state == "success":
            conditions.append(WorkflowInstance.state.in_(("SUCCESS", "FORCED_SUCCESS")))
        elif state == "failure":
            conditions.append(WorkflowInstance.state.in_(FAILURE_STATES))

        base = (
            select(WorkflowInstance, WorkflowRun, User.username)
            .join(WorkflowRun, WorkflowRun.id == WorkflowInstance.workflow_run_id)
            .join(User, User.id == WorkflowRun.user_id)
            .where(*conditions)
        )
        total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows = session.execute(
            base.order_by(WorkflowInstance.created_at.desc(), WorkflowInstance.workflow_instance_id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()

        items: list[dict[str, Any]] = []
        try:
            with DolphinSchedulerClient() as client:
                for workflow, run, username in rows:
                    try:
                        items.append(workflow_list_item(client, workflow, run, username))
                    except DolphinSchedulerError as error:
                        items.append(workflow_list_item(None, workflow, run, username, str(error)))
        except DolphinSchedulerError as error:
            items = [
                workflow_list_item(None, workflow, run, username, str(error))
                for workflow, run, username in rows
            ]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def control(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
        action: WorkflowAction,
    ) -> dict[str, Any]:
        workflow, run = self.find_accessible_workflow(session, user, workflow_instance_id)
        validate_workflow_action(workflow, action)
        if action in {WorkflowAction.RERUN, WorkflowAction.RETRY_FAILED}:
            if not workflow.is_current:
                raise RuntimeError("只能重新运行当前 workflow instance")
            if getattr(run, "saved", False):
                raise RuntimeError("已保存版本关联的 workflow instance 不能重新运行")
        with DolphinSchedulerClient() as client:
            submission = client.execute_process_instance(
                int(run.project_code or 0),
                workflow_instance_id,
                PROCESS_ACTIONS[action],
            )
            record_event(run, "WORKFLOW_CONTROL_REQUESTED", action=action.value, workflow_instance_id=workflow_instance_id)
            if action in {WorkflowAction.RERUN, WorkflowAction.RETRY_FAILED}:
                set_submission_state(run, "SUBMITTED")
            session.commit()
            synchronization_error = None
            try:
                synchronized = self.executors[run.application].synchronize(session, run, client=client)
                information = workflow_information(client, run, synchronized or workflow)
            except DolphinSchedulerError as error:
                synchronization_error = str(error)
                session.rollback()
                workflow, run = self.find_accessible_workflow(session, user, workflow_instance_id)
                information = workflow_information(None, run, workflow)
        return {
            "action": action,
            "scheduler_submission": submission,
            "synchronization_error": synchronization_error,
            "workflow": information,
        }

    def delete(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
    ) -> dict[str, Any]:
        workflow, run = self.find_accessible_workflow(session, user, workflow_instance_id)
        if workflow.state not in TERMINAL_STATES:
            raise RuntimeError(f"{workflow.state} 状态的工作流不能删除")
        if getattr(run, "saved", False):
            raise RuntimeError("已保存版本关联的工作流不能单独删除")
        instance_count = int(
            session.scalar(
                select(func.count()).select_from(WorkflowInstance).where(
                    WorkflowInstance.workflow_run_id == run.id
                )
            )
            or 0
        )
        if instance_count > 1 and workflow.is_current:
            raise RuntimeError("存在历史实例时不能单独删除当前 workflow instance")
        delete_run = instance_count <= 1
        has_artifacts = bool(
            run.output_dir
            if run.application == "incremental"
            else run.input_file
        )
        artifacts = (
            resolve_run_artifacts(run)
            if delete_run and has_artifacts
            else None
        )
        application = run.application
        record_id = run.id
        session.delete(run if delete_run else workflow)
        session.commit()
        if artifacts is not None:
            remove_run_artifacts(*artifacts)
        return {
            "application": application,
            "record_id": record_id,
            "workflow_instance_id": workflow_instance_id,
        }

    @staticmethod
    def find_accessible_workflow(
        session: Session,
        user: User,
        workflow_instance_id: int,
    ) -> tuple[WorkflowInstance, WorkflowRun]:
        statement = (
            select(WorkflowInstance, WorkflowRun)
            .join(WorkflowRun, WorkflowRun.id == WorkflowInstance.workflow_run_id)
            .where(WorkflowInstance.workflow_instance_id == workflow_instance_id)
        )
        if not user.is_admin:
            statement = statement.where(WorkflowRun.user_id == user.id)
        row = session.execute(statement).one_or_none()
        if row is None:
            raise FileNotFoundError(f"工作流实例不存在: {workflow_instance_id}")
        return row[0], row[1]

    def poll_once(self) -> int:
        engine = database_engine()
        with engine.connect() as lock_connection:
            if lock_connection.dialect.name == "postgresql":
                acquired = bool(
                    lock_connection.scalar(
                        text("SELECT pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": POLLER_LOCK_ID},
                    )
                )
                if not acquired:
                    return 0
            try:
                return self.poll_runs()
            finally:
                if lock_connection.dialect.name == "postgresql":
                    lock_connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": POLLER_LOCK_ID},
                    )

    def poll_runs(self) -> int:
        synchronized = 0
        with database_session_factory()() as session, DolphinSchedulerClient() as client:
            active_current = (
                select(WorkflowInstance.workflow_run_id)
                .where(
                    WorkflowInstance.is_current.is_(True),
                    WorkflowInstance.state.not_in(TERMINAL_STATES),
                )
            )
            statement = (
                select(WorkflowRun)
                .where(
                    or_(
                        WorkflowRun.submission_state.in_(SUBMISSION_ACTIVE_STATES),
                        WorkflowRun.id.in_(active_current),
                    )
                )
                .order_by(WorkflowRun.id)
                .limit(DolphinSchedulerSettings.POLL_BATCH_SIZE)
            )
            for run in session.scalars(statement):
                try:
                    self.executors[run.application].synchronize(session, run, client=client)
                    synchronized += 1
                except DolphinSchedulerError:
                    continue
                except Exception as error:
                    run_id = run.id
                    session.rollback()
                    failed_run = session.get(WorkflowRun, run_id)
                    if failed_run is not None:
                        failed_run.error = str(error)
                        session.commit()
                    LOGGER.exception("同步 %s workflow run %s 失败: %s", run.application, run_id, error)
        return synchronized


async def poll_workflow_statuses(stop_event: asyncio.Event) -> None:
    service = WorkflowGatewayService()
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(service.poll_once)
        except Exception:
            LOGGER.exception("DolphinScheduler 工作流状态轮询失败")
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=DolphinSchedulerSettings.POLL_INTERVAL_SECONDS,
            )
        except TimeoutError:
            continue


def current_workflow_instance(session: Session, run_id: int) -> WorkflowInstance | None:
    return session.scalar(
        select(WorkflowInstance).where(
            WorkflowInstance.workflow_run_id == run_id,
            WorkflowInstance.is_current.is_(True),
        )
    )


def workflow_information(
    client: DolphinSchedulerClient | None,
    run: WorkflowRun,
    workflow: WorkflowInstance,
) -> dict[str, Any]:
    tasks = live_workflow_tasks(client, run, workflow) if client is not None else []
    return {
        "application": run.application,
        "record_id": run.id,
        "user_id": run.user_id,
        "workflow_instance_id": workflow.workflow_instance_id,
        "project_code": int(run.project_code or 0),
        "workflow_definition_code": int(run.workflow_definition_code or 0),
        "workflow_name": str(run.workflow_name or ""),
        "state": workflow.state,
        "tasks": tasks,
        "error": workflow.error or (run.error if workflow.is_current else None),
        "started_at": workflow.started_at,
        "finished_at": workflow.finished_at,
        "duration_seconds": workflow.duration_seconds,
        "last_synced_at": workflow.last_synced_at,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
        "state_history": workflow.state_history or [],
        "events": run.events or [],
    }


def workflow_list_item(
    client: DolphinSchedulerClient | None,
    workflow: WorkflowInstance,
    run: WorkflowRun,
    username: str,
    tasks_error: str | None = None,
) -> dict[str, Any]:
    return {
        **workflow_information(client, run, workflow),
        "project_id": run.source_project_id,
        "owner_username": username,
        "payload": run.payload,
        "requested_outputs": run.requested_outputs or [],
        "tasks_error": tasks_error,
    }


def live_workflow_tasks(
    client: DolphinSchedulerClient,
    run: WorkflowRun,
    workflow: WorkflowInstance,
) -> list[dict[str, Any]]:
    instances = client.process_instance_tasks(
        project_code=int(run.project_code or 0),
        process_instance_id=workflow.workflow_instance_id,
    )
    definition = client.process_definition_details(
        int(run.project_code or 0),
        int(run.workflow_definition_code or 0),
    )
    return workflow_task_information(instances, definition)


def workflow_task_information(
    instances: list[dict[str, Any]],
    definition: dict[str, Any],
) -> list[dict[str, Any]]:
    latest_instances: dict[int, dict[str, Any]] = {}
    for instance in instances:
        task_code = integer_or_none(instance.get("taskCode"))
        task_instance_id = integer_or_none(instance.get("id"))
        if task_code is None or task_instance_id is None:
            continue
        current = latest_instances.get(task_code)
        if current is None or task_instance_id > int(current.get("id") or 0):
            latest_instances[task_code] = instance

    tasks: list[dict[str, Any]] = []
    definition_codes: set[int] = set()
    for node in ordered_workflow_definition_tasks(definition):
        task_code = int(node["code"])
        definition_codes.add(task_code)
        instance = latest_instances.get(task_code)
        tasks.append(task_information(node, instance))
    for task_code, instance in sorted(
        latest_instances.items(),
        key=lambda item: int(item[1].get("id") or 0),
    ):
        if task_code not in definition_codes:
            tasks.append(task_information({}, instance))
    return tasks


def task_information(
    definition: dict[str, Any],
    instance: dict[str, Any] | None,
) -> dict[str, Any]:
    task_code = integer_or_none((instance or {}).get("taskCode")) or integer_or_none(definition.get("code"))
    started_at = parse_scheduler_datetime((instance or {}).get("startTime"))
    finished_at = parse_scheduler_datetime((instance or {}).get("endTime"))
    return {
        "task_code": task_code,
        "task_instance_id": integer_or_none((instance or {}).get("id")),
        "name": str((instance or {}).get("name") or definition.get("name") or f"Task {task_code}"),
        "task_type": str((instance or {}).get("taskType") or definition.get("taskType") or "UNKNOWN"),
        "state": str((instance or {}).get("state") or "WAITING"),
        "host": (instance or {}).get("host"),
        "retry_times": integer_or_none((instance or {}).get("retryTimes")),
        "max_retry_times": integer_or_none((instance or {}).get("maxRetryTimes")),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds(started_at, finished_at),
    }


def ordered_workflow_definition_tasks(definition: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [
        node
        for node in definition.get("taskDefinitionList") or []
        if integer_or_none(node.get("code")) is not None
    ]
    nodes_by_code = {int(node["code"]): node for node in nodes}
    dependencies = {code: set() for code in nodes_by_code}
    for relation in definition.get("processTaskRelationList") or []:
        before = integer_or_none(relation.get("preTaskCode"))
        after = integer_or_none(relation.get("postTaskCode"))
        if before in nodes_by_code and after in nodes_by_code:
            dependencies[after].add(before)
    ordered: list[dict[str, Any]] = []
    emitted: set[int] = set()
    pending = [int(node["code"]) for node in nodes]
    while pending:
        ready = [code for code in pending if dependencies[code] <= emitted] or list(pending)
        for code in ready:
            ordered.append(nodes_by_code[code])
            emitted.add(code)
            pending.remove(code)
    return ordered


def synchronize_workflow_state(
    client: DolphinSchedulerClient,
    run: WorkflowRun,
    workflow: WorkflowInstance,
    scheduler_instance: dict[str, Any],
) -> None:
    if workflow.is_current:
        run.error = None
    apply_workflow_state(workflow, scheduler_instance)
    if workflow.state not in FAILURE_STATES:
        workflow.error = None
        return
    tasks = client.process_instance_tasks(
        project_code=int(run.project_code or 0),
        process_instance_id=workflow.workflow_instance_id,
    )
    failed = max(
        (item for item in tasks if str(item.get("state")) in FAILURE_STATES),
        key=lambda item: int(item.get("id") or 0),
        default=None,
    )
    workflow.error = (
        failure_message(client, int(failed["id"]), workflow.state)
        if failed is not None
        else f"DolphinScheduler workflow state: {workflow.state}"
    )


def apply_workflow_state(workflow: WorkflowInstance, instance: dict[str, Any]) -> None:
    state = str(instance.get("state") or workflow.state)
    if workflow.state != state:
        workflow.state = state
        workflow.state_history = [
            *(workflow.state_history or []),
            {"state": state, "timestamp": utc_now().isoformat()},
        ]
    workflow.started_at = parse_scheduler_datetime(instance.get("startTime"))
    workflow.finished_at = parse_scheduler_datetime(instance.get("endTime"))
    workflow.duration_seconds = duration_seconds(workflow.started_at, workflow.finished_at)
    workflow.last_synced_at = utc_now()


def validate_workflow_action(workflow: WorkflowInstance, action: WorkflowAction) -> None:
    if action is WorkflowAction.STOP and workflow.state in TERMINAL_STATES:
        raise RuntimeError(f"{workflow.state} 状态的工作流不能停止")
    if action is WorkflowAction.PAUSE and workflow.state in TERMINAL_STATES:
        raise RuntimeError(f"{workflow.state} 状态的工作流不能暂停")
    if action is WorkflowAction.RESUME and workflow.state != "PAUSE":
        raise RuntimeError(f"{workflow.state} 状态的工作流不能恢复")
    if action is WorkflowAction.RERUN and workflow.state not in TERMINAL_STATES:
        raise RuntimeError(f"{workflow.state} 状态的工作流不能重跑")
    if action is WorkflowAction.RETRY_FAILED and workflow.state != "FAILURE":
        raise RuntimeError(f"{workflow.state} 状态的工作流不能从失败节点续跑")


def set_submission_state(run: WorkflowRun, state: str) -> None:
    run.submission_state = state


def record_event(run: WorkflowRun, event: str, **details: Any) -> None:
    run.events = [
        *(run.events or []),
        {"event": event, "timestamp": utc_now().isoformat(), **details},
    ]


def workflow_marker(run: WorkflowRun) -> str:
    return f"{run.application}:{run.id}"


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


def failure_message(client: DolphinSchedulerClient, task_instance_id: int, state: str) -> str:
    skip_line_num = 0
    tail = ""
    for _ in range(100):
        try:
            page = client.task_log(
                task_instance_id=task_instance_id,
                skip_line_num=skip_line_num,
                limit=1000,
            )
        except DolphinSchedulerError:
            return f"DolphinScheduler task state: {state}"
        tail = (tail + page["message"])[-4000:]
        if not page["has_more"] or page["next_line_num"] == skip_line_num:
            break
        skip_line_num = page["next_line_num"]
    return tail.strip() or f"DolphinScheduler task state: {state}"


def resolve_run_directory(run: WorkflowRun) -> Path:
    if not run.input_file:
        raise RuntimeError(f"工作流记录 {run.id} 没有 input_file")
    run_directory = Path(run.input_file).resolve().parent
    expected_parent = (ArenaSettings.SHARED_DIR / run.application).resolve()
    try:
        workspace_key = UUID(run_directory.name)
    except ValueError as error:
        raise RuntimeError(f"工作流目录不是有效的 workspace key: {run_directory}") from error
    if run_directory.parent != expected_parent or workspace_key.hex != run_directory.name:
        raise RuntimeError(f"拒绝清理非工作流目录: {run_directory}")
    return run_directory


def resolve_incremental_run_directory(run: WorkflowRun) -> Path:
    """校验增量任务 output 目录并返回其 UUID 工作目录。"""
    if not run.output_dir or is_cloud_output(run.output_dir):
        raise RuntimeError(f"增量工作流记录 {run.id} 没有本地 output_dir")
    output_dir = Path(run.output_dir).resolve()
    run_directory = output_dir.parent
    expected_parent = (ArenaSettings.SHARED_DIR / "incremental").resolve()
    try:
        workspace_key = UUID(run_directory.name)
    except ValueError as error:
        raise RuntimeError(
            f"增量工作流目录不是有效的 workspace key: {run_directory}"
        ) from error
    if (
        output_dir.name != "output"
        or run_directory.parent != expected_parent
        or workspace_key.hex != run_directory.name
    ):
        raise RuntimeError(f"拒绝清理非增量工作流目录: {run_directory}")
    return run_directory


def resolve_run_artifacts(run: WorkflowRun) -> tuple[Path, str | None]:
    """返回经过应用类型校验的一次工作流产物目录。"""
    run_directory = (
        resolve_incremental_run_directory(run)
        if run.application == "incremental"
        else resolve_run_directory(run)
    )
    return run_directory, run.output_dir


def remove_run_artifacts(run_directory: Path, output_dir: str | None) -> None:
    """清理一次工作流的本地输入目录及可选云端结果目录。"""
    delete_result_objects(output_dir)
    if run_directory.exists():
        shutil.rmtree(run_directory)
