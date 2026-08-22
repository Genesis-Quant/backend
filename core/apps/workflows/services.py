"""Workflow submission, synchronization, querying, control, and polling."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select, text, union_all
from sqlalchemy.orm import Session, aliased, load_only

from config import DolphinSchedulerSettings
from core.apps.admin.models import IncrementalWorkflowWorkspace
from core.apps.backtest.models import BacktestOptimization, BacktestProject, BacktestResearch, BacktestVersion
from core.apps.factor.models import FactorProject, FactorVersion
from core.apps.query.models import QueryProject
from core.apps.users.models import User
from core.apps.workflows.artifacts import (
    runtime_output_argument,
    uses_cloud_output,
    workspace_directory,
    workspace_input_file,
    workspace_output_directory,
)
from core.apps.workflows.models import WorkflowAttempt, WorkflowInstance, WorkflowWorkspace
from core.apps.workflows.schemas import WorkflowAction
from core.database.session import database_engine, database_session_factory
from core.scheduler.applications.incremental import (
    ensure_incremental_workflow_definition,
    normalize_incremental_channel,
    normalize_incremental_workers,
)
from core.scheduler.client import DolphinSchedulerClient
from core.scheduler.domain import (
    APPLICATION_START_PARAMETERS,
    APPLICATIONS,
    FAILURE_STATES,
    INCREMENTAL_START_PARAMETERS,
    TERMINAL_STATES,
    RunApplication,
    validate_start_parameters,
)
from core.scheduler.errors import DolphinSchedulerError
from core.scheduler.metadata import workflow_definition_details
from core.scheduler.workflows import (
    ensure_workflow_definition,
)
from core.utils.results import delete_result_objects
from core.utils.time import utc_now

LOGGER = logging.getLogger(__name__)
SCHEDULER_TIMEZONE = ZoneInfo("Asia/Shanghai")
POLLER_LOCK_ID = 280284398913
WORKFLOW_RETRY_PENDING_STATE = "RETRYING"
SUBMISSION_ACTIVE_STATES = frozenset(
    {"CREATED", "SUBMITTING", "SUBMITTED", WORKFLOW_RETRY_PENDING_STATE}
)
BATCH_PENDING_STATE = "QUEUED"
AUTO_SAVE_PENDING_STATE = "AUTO_SAVE_PENDING"
ATTEMPT_FAILURE_STATES = frozenset({"SUBMIT_FAILED", "AUTO_SAVE_FAILED"})
WORKSPACE_FAILURE_STATES = FAILURE_STATES | ATTEMPT_FAILURE_STATES
WORKSPACE_TERMINAL_STATES = TERMINAL_STATES | {"AUTO_SAVE_FAILED"}
ATTEMPT_CONTEXT_EVENTS = frozenset({"AUTO_SAVE_VERSION", "BACKTEST_RESEARCH"})
PROCESS_ACTIONS = {
    WorkflowAction.STOP: "STOP",
    WorkflowAction.PAUSE: "PAUSE",
    WorkflowAction.RESUME: "RECOVER_SUSPENDED_PROCESS",
    WorkflowAction.RETRY_FAILED: "START_FAILURE_TASK_PROCESS",
}


def create_workflow_attempt(
    session: Session,
    workspace: WorkflowWorkspace,
    input_json: dict[str, Any],
    requested_outputs: Sequence[str],
    *,
    start_parameters: dict[str, str] | None = None,
    project_code: int | None = None,
    workflow_definition_code: int | None = None,
    workflow_name: str | None = None,
    submission_state: str = "CREATED",
    events: Sequence[dict[str, Any]] | None = None,
) -> WorkflowAttempt:
    """Create the sole current attempt for a workspace."""
    current = current_workflow_attempt(session, workspace.id)
    if current is not None:
        current.is_current = False
        session.flush()
    attempt = WorkflowAttempt(
        workflow_workspace_id=workspace.id,
        is_current=True,
        submission_state=submission_state,
        project_code=project_code,
        workflow_definition_code=workflow_definition_code,
        workflow_name=workflow_name,
        input_json=input_json,
        start_parameters=start_parameters or {},
        requested_outputs=list(requested_outputs),
        events=[dict(event) for event in events or []],
    )
    session.add(attempt)
    session.flush()
    return attempt


def prepare_workspace(
    workspace: WorkflowWorkspace,
    attempt: WorkflowAttempt,
    *,
    create_directory: bool,
) -> None:
    """Create or reset one validated workspace before starting an attempt."""
    input_file = workspace_input_file(workspace.application, workspace.workspace_key)
    temporary = input_file.with_suffix(".json.tmp")
    if input_file.is_symlink() or temporary.is_symlink():
        raise ValueError(f"workspace 输入文件不能是符号链接: {input_file}")

    prepare_workspace_output(workspace, create_directory=create_directory)

    temporary.write_text(
        json.dumps(
            attempt.input_json,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(input_file)


def prepare_workspace_output(
    workspace: WorkflowWorkspace,
    *,
    create_directory: bool,
) -> None:
    """Create or reset only the output area of a validated workspace."""
    run_directory = workspace_directory(
        workspace.application,
        workspace.workspace_key,
    )
    output_directory = workspace_output_directory(
        workspace.application,
        workspace.workspace_key,
    )

    if create_directory:
        if uses_cloud_output(workspace.application):
            run_directory.mkdir(parents=True, exist_ok=False)
        else:
            output_directory.mkdir(parents=True, exist_ok=False)
    else:
        if not run_directory.is_dir():
            raise OSError(f"待复用的 workspace 不存在: {run_directory}")
        if output_directory.is_symlink():
            raise ValueError(
                f"workspace 输出目录不能是符号链接: {output_directory}"
            )
        if output_directory.exists() and not output_directory.is_dir():
            raise OSError(f"workspace 输出路径不是目录: {output_directory}")
        if uses_cloud_output(workspace.application):
            delete_result_objects(workspace.application, workspace.workspace_key)
        if output_directory.exists():
            shutil.rmtree(output_directory)
        if not uses_cloud_output(workspace.application):
            output_directory.mkdir(parents=True)


class WorkflowExecutionService:
    def __init__(
        self,
        application: RunApplication,
        submission_attempts: int = 40,
        submission_interval: float = 0.25,
    ) -> None:
        self.application = application
        self.submission_attempts = submission_attempts
        self.submission_interval = submission_interval

    def resubmit_workspace(
        self,
        session: Session,
        workspace: WorkflowWorkspace,
        payload: dict[str, Any],
        outputs: list[str],
    ) -> WorkflowWorkspace:
        """Submit another attempt in the workspace of an unsaved version."""
        if workspace.application != self.application:
            raise ValueError(
                f"工作流应用不匹配: {workspace.application} != {self.application}"
            )
        if workspace_has_saved_version(session, workspace):
            raise RuntimeError(f"已保存的工作空间 {workspace.id} 不能再次提交")
        create_workflow_attempt(session, workspace, payload, outputs)
        return self.submit_workspace(session, workspace, create_directory=False)

    def submit_workspace(
        self,
        session: Session,
        workspace: WorkflowWorkspace,
        *,
        create_directory: bool,
        wait_for_workflow: bool = True,
    ) -> WorkflowWorkspace:
        attempt = require_current_workflow_attempt(session, workspace.id)
        attempt_id = attempt.id
        inherited_marker = attempt.start_parameters.get("job_id")
        run_directory = workspace_directory(
            self.application,
            workspace.workspace_key,
        )
        input_file = workspace_input_file(
            self.application,
            workspace.workspace_key,
        )
        output_argument = runtime_output_argument(
            self.application,
            workspace.workspace_key,
        )
        try:
            if not output_argument:
                raise RuntimeError(
                    f"{self.application} 工作流缺少运行所需的输入或输出路径"
                )
            if self.application == "incremental":
                raise RuntimeError("增量更新必须使用专用提交方法")
            if inherited_marker and (attempt.project_code is None or attempt.workflow_definition_code is None):
                raise RuntimeError("恢复工作流提交缺少原调度器项目或工作流定义编码")

            with DolphinSchedulerClient() as client:
                if inherited_marker:
                    attempt.submission_state = "SUBMITTING"
                    session.commit()
                    existing = self.reconcile_workflow_instance(session, workspace, client)
                    if existing is not None:
                        record_event(attempt, "WORKFLOW_RECONCILED", workflow_instance_id=existing.workflow_instance_id)
                        session.commit()
                        return workspace
                project_code, definition = ensure_workflow_definition(self.application)
                definition_code = int(definition["code"])
                attempt.project_code = project_code
                attempt.workflow_definition_code = definition_code
                attempt.workflow_name = str(definition["name"])
                start_parameters = validate_start_parameters(
                    {
                        "input_file": str(input_file),
                        "output_dir": output_argument,
                        "job_id": workflow_marker(workspace, attempt),
                        "output": " ".join(attempt.requested_outputs),
                        "cloud": str(uses_cloud_output(self.application)).lower(),
                    },
                    APPLICATION_START_PARAMETERS,
                )
                attempt.start_parameters = start_parameters
                attempt.submission_state = "SUBMITTING"
                session.commit()
                prepare_workspace(
                    workspace,
                    attempt,
                    create_directory=create_directory,
                )
                client.start_process_instance(
                    project_code=project_code,
                    process_definition_code=definition_code,
                    start_params=start_parameters,
                )
                attempt.submission_state = "SUBMITTED"
                attempt.error = None
                session.commit()
                if wait_for_workflow:
                    self.wait_for_workflow_instance(session, workspace, client)
            return workspace
        except (DolphinSchedulerError, OSError, RuntimeError, ValueError) as error:
            session.rollback()
            failed_attempt = session.get(WorkflowAttempt, attempt_id)
            if failed_attempt is not None:
                if failed_attempt.submission_state in {"CREATED", BATCH_PENDING_STATE, "SUBMITTING"}:
                    failed_attempt.submission_state = "SUBMIT_FAILED"
                failed_attempt.error = str(error)
                session.commit()
            elif create_directory and run_directory.exists():
                shutil.rmtree(run_directory)
            raise

    def reconcile_workflow_instance(
        self,
        session: Session,
        workspace: WorkflowWorkspace,
        client: DolphinSchedulerClient,
    ) -> WorkflowInstance | None:
        last_error: DolphinSchedulerError | None = None
        attempts = self.submission_attempts
        for index in range(attempts):
            try:
                workflow = self.synchronize(session, workspace, client=client)
                last_error = None
                if workflow is not None:
                    return workflow
            except DolphinSchedulerError as error:
                last_error = error
            if index + 1 < attempts:
                time.sleep(self.submission_interval)
        if last_error is not None:
            raise last_error
        return None

    def wait_for_workflow_instance(
        self,
        session: Session,
        workspace: WorkflowWorkspace,
        client: DolphinSchedulerClient,
    ) -> WorkflowInstance:
        last_error: DolphinSchedulerError | None = None
        for attempt in range(self.submission_attempts):
            try:
                workflow = self.synchronize(session, workspace, client=client)
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
        require_current_workflow_attempt(session, workspace.id).error = message
        session.commit()
        raise DolphinSchedulerError(message)

    def synchronize(
        self,
        session: Session,
        workspace: WorkflowWorkspace,
        client: DolphinSchedulerClient | None = None,
    ) -> WorkflowInstance | None:
        attempt = require_current_workflow_attempt(session, workspace.id)
        if attempt.project_code is None or attempt.workflow_definition_code is None:
            return None
        if client is None:
            with DolphinSchedulerClient() as active_client:
                return self.synchronize(session, workspace, client=active_client)

        workflow = current_workflow_instance(session, workspace.id)
        scheduler_instance = None
        retrying_failed_tasks = (
            attempt.submission_state == WORKFLOW_RETRY_PENDING_STATE
        )
        if workflow is None or (
            attempt.submission_state in SUBMISSION_ACTIVE_STATES
            and not retrying_failed_tasks
        ):
            scheduler_instance = self.locate_new_workflow_instance(session, client, workspace, attempt)
            if scheduler_instance is not None:
                workflow_instance_id = int(scheduler_instance["id"])
                workflow = session.get(WorkflowInstance, workflow_instance_id)
                if workflow is None:
                    workflow = WorkflowInstance(
                        workflow_instance_id=workflow_instance_id,
                        workflow_attempt_id=attempt.id,
                        state=str(scheduler_instance.get("state") or "SUBMITTED_SUCCESS"),
                        state_history=[],
                    )
                    session.add(workflow)
                elif workflow.workflow_attempt_id != attempt.id:
                    raise RuntimeError(f"DolphinScheduler 实例 {workflow_instance_id} 已属于其它提交尝试")
                attempt.submission_state = "WORKFLOW_CREATED"

        if workflow is None:
            return None
        if scheduler_instance is None:
            scheduler_instance = client.process_instance(
                int(attempt.project_code),
                workflow.workflow_instance_id,
            )
            scheduler_state = str(
                scheduler_instance.get("state") or workflow.state
            )
            if retrying_failed_tasks:
                baseline_task_id = retry_baseline_task_instance_id(attempt)
                latest_task_id = max(
                    (
                        int(item.get("id") or 0)
                        for item in client.process_instance_tasks(
                            project_code=int(attempt.project_code),
                            process_instance_id=workflow.workflow_instance_id,
                        )
                    ),
                    default=0,
                )
                if scheduler_state != "FAILURE" or latest_task_id > baseline_task_id:
                    attempt.submission_state = "WORKFLOW_CREATED"
            elif attempt.submission_state in SUBMISSION_ACTIVE_STATES:
                attempt.submission_state = "WORKFLOW_CREATED"

        synchronize_workflow_state(client, workspace, attempt, workflow, scheduler_instance)
        session.commit()
        return workflow

    def locate_new_workflow_instance(
        self,
        session: Session,
        client: DolphinSchedulerClient,
        workspace: WorkflowWorkspace,
        attempt: WorkflowAttempt,
    ) -> dict[str, Any] | None:
        known_ids = set(
            session.scalars(
                select(WorkflowInstance.workflow_instance_id).where(
                    WorkflowInstance.workflow_attempt_id.in_(
                        select(WorkflowAttempt.id).where(WorkflowAttempt.workflow_workspace_id == workspace.id)
                    )
                )
            )
        )
        marker = workflow_marker(workspace, attempt)
        for page_no in range(1, 21):
            instances = client.process_instances(
                project_code=int(attempt.project_code or 0),
                process_definition_code=int(attempt.workflow_definition_code or 0),
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
        overwrite: bool = False,
    ) -> tuple[WorkflowWorkspace, Any]:
        selected_workers = normalize_incremental_workers(workers)
        selected_channel = normalize_incremental_channel(channel)
        workspace = WorkflowWorkspace(user_id=user_id, application="incremental")
        session.add(workspace)
        session.flush()
        session.add(IncrementalWorkflowWorkspace(id=workspace.id))
        attempt = create_workflow_attempt(session, workspace, {}, [])
        submission = self.submit_incremental_attempt(
            session,
            workspace,
            attempt,
            selected_workers,
            selected_channel,
            overwrite,
            create_directory=True,
        )
        return workspace, submission

    def rerun_incremental(
        self,
        session: Session,
        workspace: WorkflowWorkspace,
        previous_attempt: WorkflowAttempt,
    ) -> tuple[WorkflowWorkspace, Any]:
        """Rerun an incremental workspace with its previous validated options."""
        if workspace.application != "incremental":
            raise ValueError("只能用增量更新执行器重跑 incremental workspace")
        parameters = previous_attempt.start_parameters
        workers = parameters.get("workers")
        channel = parameters.get("channel")
        overwrite = parameters.get("overwrite")
        if not workers or not channel or overwrite not in {"true", "false"}:
            raise RuntimeError("增量更新历史 Attempt 缺少可重跑的提交参数")
        selected_workers = normalize_incremental_workers(workers.split(","))
        selected_channel = normalize_incremental_channel(channel)
        previous_workflow = workflow_instance_for_attempt(
            session,
            previous_attempt.id,
        )
        attempt = create_workflow_attempt(
            session,
            workspace,
            {},
            [],
            events=attempt_context_events(previous_attempt),
        )
        record_event(
            attempt,
            "WORKFLOW_CONTROL_REQUESTED",
            action=WorkflowAction.RERUN.value,
            previous_attempt_id=previous_attempt.id,
            workflow_instance_id=(
                previous_workflow.workflow_instance_id
                if previous_workflow is not None
                else None
            ),
        )
        submission = self.submit_incremental_attempt(
            session,
            workspace,
            attempt,
            selected_workers,
            selected_channel,
            overwrite == "true",
            create_directory=False,
        )
        return workspace, submission

    def submit_incremental_attempt(
        self,
        session: Session,
        workspace: WorkflowWorkspace,
        attempt: WorkflowAttempt,
        selected_workers: Sequence[str],
        selected_channel: str,
        overwrite: bool,
        *,
        create_directory: bool,
    ) -> Any:
        """Submit one prepared Incremental Attempt."""
        attempt_id = attempt.id
        submission: Any = None
        run_directory: Path | None = None
        try:
            run_directory = workspace_directory("incremental", workspace.workspace_key)
            output_dir = workspace_output_directory("incremental", workspace.workspace_key)
            prepare_workspace_output(
                workspace,
                create_directory=create_directory,
            )
            project_code, definition = ensure_incremental_workflow_definition()
            definition_code = int(definition["code"])
            attempt.project_code = project_code
            attempt.workflow_definition_code = definition_code
            attempt.workflow_name = str(definition["name"])
            start_parameters = validate_start_parameters(
                {
                    "job_id": workflow_marker(workspace, attempt),
                    "output_dir": str(output_dir),
                    "workers": ",".join(selected_workers),
                    "channel": selected_channel,
                    "overwrite": "true" if overwrite else "false",
                },
                INCREMENTAL_START_PARAMETERS,
            )
            attempt.start_parameters = start_parameters
            attempt.submission_state = "SUBMITTING"
            session.commit()
            with DolphinSchedulerClient() as client:
                submission = client.start_process_instance(
                    project_code=project_code,
                    process_definition_code=definition_code,
                    start_params=start_parameters,
                    failure_strategy="CONTINUE",
                )
                attempt.submission_state = "SUBMITTED"
                record_event(attempt, "WORKFLOW_SUBMITTED")
                session.commit()
                self.wait_for_workflow_instance(session, workspace, client)
            return submission
        except (DolphinSchedulerError, OSError, RuntimeError, ValueError) as error:
            session.rollback()
            failed_attempt = session.get(WorkflowAttempt, attempt_id)
            if failed_attempt is not None:
                if failed_attempt.submission_state in {"CREATED", "SUBMITTING"}:
                    failed_attempt.submission_state = "SUBMIT_FAILED"
                failed_attempt.error = str(error)
                session.commit()
            elif create_directory and run_directory is not None and run_directory.exists():
                shutil.rmtree(run_directory)
            raise


class WorkflowGatewayService:
    def __init__(self) -> None:
        self.executors: dict[str, WorkflowExecutionService] = {
            application: WorkflowExecutionService(application)
            for application in APPLICATIONS
        }
        self.executors["incremental"] = IncrementalWorkflowExecutionService("incremental")

    def submit_incremental(
        self,
        session: Session,
        user_id: int,
        workers: Sequence[str] | None = None,
        channel: str | None = None,
        overwrite: bool = False,
    ) -> tuple[WorkflowWorkspace, Any]:
        executor = self.executors["incremental"]
        if not isinstance(executor, IncrementalWorkflowExecutionService):
            raise RuntimeError("增量更新工作流执行器配置错误")
        return executor.submit_incremental(
            session,
            user_id,
            workers,
            channel,
            overwrite,
        )

    def status(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
    ) -> dict[str, Any]:
        workflow, attempt, workspace = self.find_accessible_workflow(session, user, workflow_instance_id)
        with DolphinSchedulerClient() as client:
            scheduler_instance = client.process_instance(
                int(attempt.project_code or 0),
                workflow.workflow_instance_id,
            )
            synchronize_workflow_state(client, workspace, attempt, workflow, scheduler_instance)
            session.commit()
            return workflow_status_information(attempt, workflow)

    def workspace_status(
        self,
        session: Session,
        user: User,
        workspace_id: int,
    ) -> dict[str, Any]:
        statement = (
            select(
                WorkflowAttempt.submission_state,
                WorkflowAttempt.error.label("attempt_error"),
                WorkflowAttempt.events,
                WorkflowAttempt.updated_at.label("attempt_updated_at"),
                WorkflowInstance.workflow_instance_id,
                WorkflowInstance.state.label("workflow_state"),
                WorkflowInstance.error.label("workflow_error"),
                WorkflowInstance.updated_at.label("workflow_updated_at"),
            )
            .select_from(WorkflowWorkspace)
            .join(
                WorkflowAttempt,
                and_(
                    WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id,
                    WorkflowAttempt.is_current.is_(True),
                ),
            )
            .outerjoin(
                WorkflowInstance,
                WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id,
            )
            .where(WorkflowWorkspace.id == workspace_id)
        )
        if not user.is_admin:
            statement = statement.where(WorkflowWorkspace.user_id == user.id)
        row = session.execute(statement).one_or_none()
        if row is None:
            raise FileNotFoundError(f"工作流工作空间不存在: {workspace_id}")
        state = (
            row.submission_state
            if row.submission_state
            in {
                AUTO_SAVE_PENDING_STATE,
                "AUTO_SAVE_FAILED",
                WORKFLOW_RETRY_PENDING_STATE,
            }
            else row.workflow_state or row.submission_state
        )
        return {
            "workflow_instance_id": row.workflow_instance_id,
            "state": state,
            "error": row.workflow_error or row.attempt_error,
            "events": row.events,
            "updated_at": max(row.attempt_updated_at, row.workflow_updated_at) if row.workflow_updated_at is not None else row.attempt_updated_at,
        }

    def detail(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
    ) -> dict[str, Any]:
        workflow, attempt, workspace = self.find_accessible_workflow(session, user, workflow_instance_id)
        try:
            with DolphinSchedulerClient() as client:
                tasks = live_workflow_tasks(client, attempt, workflow)
            tasks_error = None
        except DolphinSchedulerError as error:
            tasks = []
            tasks_error = str(error)
        return workflow_information(workspace, attempt, workflow, tasks, tasks_error)

    def tasks(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
    ) -> dict[str, Any]:
        workflow, attempt, workspace = self.find_accessible_workflow(session, user, workflow_instance_id)
        with DolphinSchedulerClient() as client:
            return workflow_tasks(client, attempt, workflow)

    def list(
        self,
        session: Session,
        user: User,
        page: int,
        page_size: int,
        application: str | None,
        state: str | None,
    ) -> dict[str, Any]:
        attempt_counter = aliased(WorkflowAttempt)
        attempt_count = (
            select(func.count(attempt_counter.id))
            .where(attempt_counter.workflow_workspace_id == WorkflowWorkspace.id)
            .correlate(WorkflowWorkspace)
            .scalar_subquery()
        )
        position_counter = aliased(WorkflowAttempt)
        attempt_number = (
            select(func.count(position_counter.id))
            .where(
                position_counter.workflow_workspace_id == WorkflowWorkspace.id,
                or_(
                    position_counter.created_at < WorkflowAttempt.created_at,
                    and_(
                        position_counter.created_at == WorkflowAttempt.created_at,
                        position_counter.id <= WorkflowAttempt.id,
                    ),
                ),
            )
            .correlate(WorkflowWorkspace, WorkflowAttempt)
            .scalar_subquery()
        )
        conditions = [] if user.is_admin else [WorkflowWorkspace.user_id == user.id]
        if application is not None:
            conditions.append(WorkflowWorkspace.application == application)
        if state == "active":
            conditions.append(
                or_(
                    WorkflowAttempt.submission_state == AUTO_SAVE_PENDING_STATE,
                    WorkflowAttempt.submission_state.in_(SUBMISSION_ACTIVE_STATES),
                    and_(
                        WorkflowInstance.workflow_instance_id.is_not(None),
                        WorkflowInstance.state.not_in(TERMINAL_STATES),
                        WorkflowAttempt.submission_state.not_in(ATTEMPT_FAILURE_STATES),
                    ),
                )
            )
        elif state == "success":
            conditions.append(
                and_(
                    WorkflowInstance.state.in_(("SUCCESS", "FORCED_SUCCESS")),
                    WorkflowAttempt.submission_state != AUTO_SAVE_PENDING_STATE,
                    WorkflowAttempt.submission_state.not_in(ATTEMPT_FAILURE_STATES),
                )
            )
        elif state == "failure":
            conditions.append(
                or_(
                    WorkflowAttempt.submission_state.in_(ATTEMPT_FAILURE_STATES),
                    and_(
                        WorkflowInstance.state.in_(FAILURE_STATES),
                        WorkflowAttempt.submission_state.not_in(
                            SUBMISSION_ACTIVE_STATES
                        ),
                    ),
                )
            )

        base = (
            select(
                WorkflowWorkspace,
                WorkflowAttempt,
                WorkflowInstance,
                User.username,
                attempt_count.label("attempt_count"),
                attempt_number.label("attempt_number"),
            )
            .join(
                WorkflowAttempt,
                and_(
                    WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id,
                    WorkflowAttempt.is_current.is_(True),
                ),
            )
            .outerjoin(
                WorkflowInstance,
                WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id,
            )
            .join(User, User.id == WorkflowWorkspace.user_id)
            .where(*conditions)
        )
        total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows = session.execute(
            base.options(
                load_only(
                    WorkflowWorkspace.id,
                    WorkflowWorkspace.user_id,
                    WorkflowWorkspace.application,
                ),
                load_only(
                    WorkflowAttempt.id,
                    WorkflowAttempt.workflow_workspace_id,
                    WorkflowAttempt.is_current,
                    WorkflowAttempt.submission_state,
                    WorkflowAttempt.project_code,
                    WorkflowAttempt.workflow_definition_code,
                    WorkflowAttempt.created_at,
                    WorkflowAttempt.updated_at,
                ),
                load_only(
                    WorkflowInstance.workflow_instance_id,
                    WorkflowInstance.workflow_attempt_id,
                    WorkflowInstance.state,
                    WorkflowInstance.started_at,
                    WorkflowInstance.finished_at,
                    WorkflowInstance.duration_seconds,
                    WorkflowInstance.updated_at,
                ),
            )
            .order_by(WorkflowAttempt.created_at.desc(), WorkflowAttempt.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        if not rows:
            return {"items": [], "total": total, "page": page, "page_size": page_size}
        project_references = workspace_project_references(session, [row[0] for row in rows])

        items: list[dict[str, Any]] = []
        try:
            with DolphinSchedulerClient() as client:
                for workspace, attempt, workflow, username, count, number in rows:
                    try:
                        summary = workflow_attempt_summary(
                            client,
                            attempt,
                            workflow,
                            int(number),
                        )
                    except DolphinSchedulerError as error:
                        summary = workflow_attempt_summary(
                            None,
                            attempt,
                            workflow,
                            int(number),
                            str(error),
                        )
                    reference = project_references.get(workspace.id)
                    items.append(
                        workflow_workspace_list_item(
                            workspace,
                            str(username),
                            int(count),
                            summary,
                            reference,
                        )
                    )
        except DolphinSchedulerError as error:
            items = [
                workflow_workspace_list_item(
                    workspace,
                    str(username),
                    int(count),
                    workflow_attempt_summary(
                        None,
                        attempt,
                        workflow,
                        int(number),
                        str(error) if workflow is not None else None,
                    ),
                    project_references.get(workspace.id),
                )
                for workspace, attempt, workflow, username, count, number in rows
            ]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def attempts(
        self,
        session: Session,
        user: User,
        workspace_id: int,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        workspace = self.find_accessible_workspace(session, user, workspace_id)
        base = select(WorkflowAttempt).where(
            WorkflowAttempt.workflow_workspace_id == workspace.id
        )
        total = int(
            session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        attempts = list(
            session.scalars(
                base
                .options(
                    load_only(
                        WorkflowAttempt.id,
                        WorkflowAttempt.workflow_workspace_id,
                        WorkflowAttempt.is_current,
                        WorkflowAttempt.submission_state,
                        WorkflowAttempt.workflow_definition_code,
                        WorkflowAttempt.created_at,
                        WorkflowAttempt.updated_at,
                    )
                )
                .order_by(WorkflowAttempt.created_at.desc(), WorkflowAttempt.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        if not attempts:
            return {"items": [], "total": total, "page": page, "page_size": page_size}
        workflows = {
            workflow.workflow_attempt_id: workflow
            for workflow in session.scalars(
                select(WorkflowInstance)
                .options(
                    load_only(
                        WorkflowInstance.workflow_instance_id,
                        WorkflowInstance.workflow_attempt_id,
                        WorkflowInstance.state,
                        WorkflowInstance.started_at,
                        WorkflowInstance.finished_at,
                        WorkflowInstance.duration_seconds,
                        WorkflowInstance.updated_at,
                    )
                )
                .where(WorkflowInstance.workflow_attempt_id.in_([attempt.id for attempt in attempts]))
            )
        }
        numbered_attempts = [
            (
                attempt,
                workflows.get(attempt.id),
                total - (page - 1) * page_size - index,
            )
            for index, attempt in enumerate(attempts)
        ]
        return {
            "items": [
                workflow_attempt_summary(
                    None,
                    attempt,
                    workflow,
                    attempt_number,
                )
                for attempt, workflow, attempt_number in numbered_attempts
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def attempt_detail(
        self,
        session: Session,
        user: User,
        attempt_id: int,
    ) -> dict[str, Any]:
        statement = (
            select(WorkflowAttempt, WorkflowWorkspace, WorkflowInstance)
            .options(
                load_only(
                    WorkflowAttempt.id,
                    WorkflowAttempt.workflow_workspace_id,
                    WorkflowAttempt.submission_state,
                    WorkflowAttempt.project_code,
                    WorkflowAttempt.workflow_definition_code,
                    WorkflowAttempt.workflow_name,
                    WorkflowAttempt.input_json,
                    WorkflowAttempt.start_parameters,
                    WorkflowAttempt.requested_outputs,
                    WorkflowAttempt.error,
                    WorkflowAttempt.events,
                    WorkflowAttempt.created_at,
                    WorkflowAttempt.updated_at,
                ),
                load_only(
                    WorkflowWorkspace.id,
                    WorkflowWorkspace.application,
                ),
                load_only(
                    WorkflowInstance.workflow_instance_id,
                    WorkflowInstance.workflow_attempt_id,
                    WorkflowInstance.state,
                    WorkflowInstance.error,
                    WorkflowInstance.started_at,
                    WorkflowInstance.finished_at,
                    WorkflowInstance.duration_seconds,
                    WorkflowInstance.last_synced_at,
                    WorkflowInstance.state_history,
                ),
            )
            .join(
                WorkflowWorkspace,
                WorkflowWorkspace.id == WorkflowAttempt.workflow_workspace_id,
            )
            .outerjoin(
                WorkflowInstance,
                WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id,
            )
            .where(WorkflowAttempt.id == attempt_id)
        )
        if not user.is_admin:
            statement = statement.where(WorkflowWorkspace.user_id == user.id)
        row = session.execute(statement).one_or_none()
        if row is None:
            raise FileNotFoundError(f"工作流运行记录不存在: {attempt_id}")
        attempt, workspace, workflow = row
        attempt_number = int(
            session.scalar(
                select(func.count())
                .select_from(WorkflowAttempt)
                .where(
                    WorkflowAttempt.workflow_workspace_id == workspace.id,
                    or_(
                        WorkflowAttempt.created_at < attempt.created_at,
                        and_(
                            WorkflowAttempt.created_at == attempt.created_at,
                            WorkflowAttempt.id <= attempt.id,
                        ),
                    ),
                )
            )
            or 0
        )
        reference = workspace_project_references(session, [workspace]).get(workspace.id)
        definition: dict[str, Any] = {}
        if attempt.workflow_definition_code is not None:
            try:
                definition = workflow_definition_details(
                    int(attempt.workflow_definition_code)
                )
            except DolphinSchedulerError:
                definition = {}
        return {
            "application": workspace.application,
            "workspace_id": workspace.id,
            "project_title": reference[1] if reference is not None else None,
            "attempt_id": attempt.id,
            "attempt_number": attempt_number,
            "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
            "project_code": attempt.project_code,
            "workflow_definition_code": attempt.workflow_definition_code,
            "workflow_name": attempt.workflow_name,
            "state": workflow_attempt_state(attempt, workflow),
            "error": (workflow.error if workflow is not None else None) or attempt.error,
            "started_at": workflow.started_at if workflow is not None else None,
            "finished_at": workflow.finished_at if workflow is not None else None,
            "duration_seconds": workflow.duration_seconds if workflow is not None else None,
            "last_synced_at": workflow.last_synced_at if workflow is not None else None,
            "attempt_created_at": attempt.created_at,
            "attempt_updated_at": attempt.updated_at,
            "task_count": len(definition.get("taskDefinitionList") or []),
            "payload": {
                "input_json": attempt.input_json,
                "start_parameters": attempt.start_parameters,
            },
            "requested_outputs": attempt.requested_outputs,
            "state_history": workflow.state_history or [] if workflow is not None else [],
            "events": attempt.events or [],
        }

    def control(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
        action: WorkflowAction,
    ) -> dict[str, Any]:
        workflow, attempt, workspace = self.find_accessible_workflow(session, user, workflow_instance_id)
        validate_workflow_action(workflow, action)
        if action is WorkflowAction.RERUN:
            if not attempt.is_current:
                raise RuntimeError("只能重新运行当前 workflow instance")
            if workspace_has_saved_version(session, workspace):
                raise RuntimeError("已保存版本关联的 workflow instance 不能重新运行")
            executor = self.executors[workspace.application]
            if isinstance(executor, IncrementalWorkflowExecutionService):
                executor.rerun_incremental(session, workspace, attempt)
            else:
                previous_attempt_id = attempt.id
                previous_workflow_instance_id = workflow.workflow_instance_id
                rerun_attempt = create_workflow_attempt(
                    session,
                    workspace,
                    attempt.input_json,
                    attempt.requested_outputs,
                    events=attempt_context_events(attempt),
                )
                record_event(
                    rerun_attempt,
                    "WORKFLOW_CONTROL_REQUESTED",
                    action=action.value,
                    previous_attempt_id=previous_attempt_id,
                    workflow_instance_id=previous_workflow_instance_id,
                )
                session.commit()
                executor.submit_workspace(
                    session,
                    workspace,
                    create_directory=False,
                )
            current_attempt = require_current_workflow_attempt(session, workspace.id)
            current_workflow = current_workflow_instance(session, workspace.id)
            return {
                "workflow": workflow_status_information(
                    current_attempt,
                    current_workflow,
                )
            }
        with DolphinSchedulerClient() as client:
            retry_baseline_task_id = 0
            if action is WorkflowAction.RETRY_FAILED:
                if attempt.submission_state == WORKFLOW_RETRY_PENDING_STATE:
                    raise RuntimeError("失败节点正在续跑，请勿重复提交")
                retry_baseline_task_id = max(
                    (
                        int(item.get("id") or 0)
                        for item in client.process_instance_tasks(
                            project_code=int(attempt.project_code or 0),
                            process_instance_id=workflow_instance_id,
                        )
                    ),
                    default=0,
                )
            client.execute_process_instance(
                int(attempt.project_code or 0),
                workflow_instance_id,
                PROCESS_ACTIONS[action],
            )
            if action is WorkflowAction.RETRY_FAILED:
                attempt.submission_state = WORKFLOW_RETRY_PENDING_STATE
                record_event(
                    attempt,
                    "WORKFLOW_CONTROL_REQUESTED",
                    action=action.value,
                    workflow_instance_id=workflow_instance_id,
                    previous_task_instance_id=retry_baseline_task_id,
                )
            else:
                record_event(
                    attempt,
                    "WORKFLOW_CONTROL_REQUESTED",
                    action=action.value,
                    workflow_instance_id=workflow_instance_id,
                )
            session.commit()
            try:
                synchronized = self.executors[workspace.application].synchronize(session, workspace, client=client)
                information = workflow_status_information(attempt, synchronized or workflow)
            except DolphinSchedulerError as error:
                session.rollback()
                workflow, attempt, workspace = self.find_accessible_workflow(session, user, workflow_instance_id)
                information = workflow_status_information(attempt, workflow)
                LOGGER.warning("执行工作流操作后同步状态失败: %s", error)
        return {"workflow": information}

    def delete(
        self,
        session: Session,
        user: User,
        workflow_instance_id: int,
    ) -> dict[str, Any]:
        workflow, attempt, workspace = self.find_accessible_workflow(session, user, workflow_instance_id)
        if workflow.state not in TERMINAL_STATES:
            raise RuntimeError(f"{workflow.state} 状态的工作流不能删除")
        if workspace_has_saved_version(session, workspace):
            raise RuntimeError("已保存版本关联的工作流不能单独删除")
        version_model = {"factor": FactorVersion, "backtest": BacktestVersion}.get(workspace.application)
        if version_model is not None:
            version = session.scalar(select(version_model).where(version_model.workflow_workspace_id == workspace.id))
            if version is not None and not version.is_current:
                raise RuntimeError("批量生成的版本工作流不能单独删除")
        instance_count = int(
            session.scalar(
                select(func.count()).select_from(WorkflowInstance).where(
                    WorkflowInstance.workflow_attempt_id.in_(
                        select(WorkflowAttempt.id).where(WorkflowAttempt.workflow_workspace_id == workspace.id)
                    )
                )
            )
            or 0
        )
        if instance_count > 1 and attempt.is_current:
            raise RuntimeError("存在历史实例时不能单独删除当前 workflow instance")
        last_instance = instance_count <= 1
        if last_instance and session.scalar(select(BacktestResearch.id).where(BacktestResearch.workflow_workspace_id == workspace.id).limit(1)) is not None:
            raise RuntimeError("批量研究关联的工作流不能单独删除")
        if last_instance and session.scalar(select(BacktestOptimization.id).where(BacktestOptimization.workflow_workspace_id == workspace.id).limit(1)) is not None:
            raise RuntimeError("参数调优报告关联的工作流不能单独删除")
        artifacts = resolve_workspace_artifacts(workspace) if last_instance else None
        application = workspace.application
        workspace_id = workspace.id
        if last_instance and application == "incremental":
            session.delete(workspace)
        else:
            session.delete(workflow)
            session.delete(attempt)
        session.commit()
        if artifacts is not None:
            remove_workspace_artifacts(*artifacts)
        return {
            "application": application,
            "workspace_id": workspace_id,
            "workflow_instance_id": workflow_instance_id,
        }

    @staticmethod
    def find_accessible_workspace(
        session: Session,
        user: User,
        workspace_id: int,
    ) -> WorkflowWorkspace:
        statement = select(WorkflowWorkspace).where(WorkflowWorkspace.id == workspace_id)
        if not user.is_admin:
            statement = statement.where(WorkflowWorkspace.user_id == user.id)
        workspace = session.scalar(statement)
        if workspace is None:
            raise FileNotFoundError(f"工作流工作空间不存在: {workspace_id}")
        return workspace

    @staticmethod
    def find_accessible_workflow(
        session: Session,
        user: User,
        workflow_instance_id: int,
    ) -> tuple[WorkflowInstance, WorkflowAttempt, WorkflowWorkspace]:
        statement = (
            select(WorkflowInstance, WorkflowAttempt, WorkflowWorkspace)
            .join(WorkflowAttempt, WorkflowAttempt.id == WorkflowInstance.workflow_attempt_id)
            .join(WorkflowWorkspace, WorkflowWorkspace.id == WorkflowAttempt.workflow_workspace_id)
            .where(WorkflowInstance.workflow_instance_id == workflow_instance_id)
        )
        if not user.is_admin:
            statement = statement.where(WorkflowWorkspace.user_id == user.id)
        row = session.execute(statement).one_or_none()
        if row is None:
            raise FileNotFoundError(f"工作流实例不存在: {workflow_instance_id}")
        return row[0], row[1], row[2]

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
                return self.poll_workspaces()
            finally:
                if lock_connection.dialect.name == "postgresql":
                    lock_connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": POLLER_LOCK_ID},
                    )

    def poll_workspaces(self) -> int:
        synchronized = 0
        with database_session_factory()() as pending_session:
            pending_attempt_ids = list(
                pending_session.scalars(
                    select(WorkflowAttempt.id)
                    .where(WorkflowAttempt.submission_state == BATCH_PENDING_STATE)
                    .order_by(WorkflowAttempt.id)
                    .limit(DolphinSchedulerSettings.POLL_BATCH_SIZE)
                )
            )
        submit_attempts_now(pending_attempt_ids)
        with database_session_factory()() as session, DolphinSchedulerClient() as client:
            active_current = (
                select(WorkflowAttempt.workflow_workspace_id)
                .join(WorkflowInstance, WorkflowInstance.workflow_attempt_id == WorkflowAttempt.id)
                .where(
                    WorkflowAttempt.is_current.is_(True),
                    WorkflowInstance.state.not_in(TERMINAL_STATES),
                )
            )
            statement = (
                select(WorkflowWorkspace, WorkflowAttempt)
                .join(WorkflowAttempt, WorkflowAttempt.workflow_workspace_id == WorkflowWorkspace.id)
                .where(
                    WorkflowAttempt.is_current.is_(True),
                    or_(
                        WorkflowAttempt.submission_state.in_(SUBMISSION_ACTIVE_STATES),
                        WorkflowWorkspace.id.in_(active_current),
                        WorkflowAttempt.submission_state == AUTO_SAVE_PENDING_STATE,
                    )
                )
                .order_by(WorkflowWorkspace.id)
                .limit(DolphinSchedulerSettings.POLL_BATCH_SIZE)
            )
            for workspace, attempt in session.execute(statement):
                try:
                    executor = self.executors[workspace.application]
                    executor.synchronize(session, workspace, client=client)
                    finalize_project_auto_save_workspace(session, workspace)
                    synchronized += 1
                except DolphinSchedulerError:
                    continue
                except Exception as error:
                    attempt_id = attempt.id
                    session.rollback()
                    failed_attempt = session.get(WorkflowAttempt, attempt_id)
                    if failed_attempt is not None:
                        failed_attempt.error = str(error)
                        session.commit()
                    LOGGER.exception("同步 %s workflow attempt %s 失败", workspace.application, attempt_id)
        return synchronized


def finalize_project_auto_save_workspace(session: Session, run: WorkflowWorkspace) -> None:
    if run.application == "factor":
        from core.apps.factor.services import finalize_factor_auto_save_workspace

        finalize_factor_auto_save_workspace(session, run)
    elif run.application == "backtest":
        from core.apps.backtest.services import finalize_backtest_auto_save_workspace

        finalize_backtest_auto_save_workspace(session, run)
    elif run.application == "sensitivity":
        from core.apps.backtest.services import finalize_sensitivity_workspace

        finalize_sensitivity_workspace(session, run)


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


def current_workflow_attempt(session: Session, workspace_id: int) -> WorkflowAttempt | None:
    return session.scalar(
        select(WorkflowAttempt).where(
            WorkflowAttempt.workflow_workspace_id == workspace_id,
            WorkflowAttempt.is_current.is_(True),
        )
    )


def require_current_workflow_attempt(session: Session, workspace_id: int) -> WorkflowAttempt:
    attempt = current_workflow_attempt(session, workspace_id)
    if attempt is None:
        raise RuntimeError(f"工作流工作空间 {workspace_id} 没有当前提交尝试")
    return attempt


def workflow_instance_for_attempt(session: Session, attempt_id: int) -> WorkflowInstance | None:
    return session.scalar(select(WorkflowInstance).where(WorkflowInstance.workflow_attempt_id == attempt_id))


def current_workflow_instance(session: Session, workspace_id: int) -> WorkflowInstance | None:
    return session.scalar(
        select(WorkflowInstance)
        .join(WorkflowAttempt, WorkflowAttempt.id == WorkflowInstance.workflow_attempt_id)
        .where(
            WorkflowAttempt.workflow_workspace_id == workspace_id,
            WorkflowAttempt.is_current.is_(True),
        )
    )


def workflow_workspace_state(session: Session, workspace: WorkflowWorkspace) -> str:
    attempt = current_workflow_attempt(session, workspace.id)
    if attempt is None:
        return "DRAFT"
    workflow = workflow_instance_for_attempt(session, attempt.id)
    return workflow_attempt_state(attempt, workflow)


def workspace_has_saved_version(session: Session, workspace: WorkflowWorkspace) -> bool:
    version_model = {"factor": FactorVersion, "backtest": BacktestVersion}.get(workspace.application)
    if version_model is None:
        return False
    return session.scalar(select(version_model.id).where(version_model.workflow_workspace_id == workspace.id, version_model.saved.is_(True)).limit(1)) is not None


def workflow_status_information(
    attempt: WorkflowAttempt,
    workflow: WorkflowInstance,
) -> dict[str, Any]:
    return {
        "state": workflow_attempt_state(attempt, workflow),
        "error": workflow.error or attempt.error,
    }


def workflow_information(
    workspace: WorkflowWorkspace,
    attempt: WorkflowAttempt,
    workflow: WorkflowInstance,
    tasks: list[dict[str, Any]],
    tasks_error: str | None,
) -> dict[str, Any]:
    definition = workflow_definition_details(int(attempt.workflow_definition_code or 0))
    return {
        "application": workspace.application,
        "workspace_id": workspace.id,
        "user_id": workspace.user_id,
        "workflow_instance_id": workflow.workflow_instance_id,
        "project_code": int(attempt.project_code or 0),
        "workflow_definition_code": int(attempt.workflow_definition_code or 0),
        "workflow_name": str(attempt.workflow_name or ""),
        "state": workflow.state,
        "error": workflow.error or attempt.error,
        "started_at": workflow.started_at,
        "finished_at": workflow.finished_at,
        "duration_seconds": workflow.duration_seconds,
        "last_synced_at": workflow.last_synced_at,
        "created_at": workflow.created_at,
        "updated_at": workflow.updated_at,
        "task_count": len(definition.get("taskDefinitionList") or []),
        "tasks": tasks,
        "tasks_error": tasks_error,
        "payload": {"input_json": attempt.input_json, "start_parameters": attempt.start_parameters},
        "requested_outputs": attempt.requested_outputs,
        "state_history": workflow.state_history or [],
        "events": attempt.events or [],
    }


def workflow_tasks(
    client: DolphinSchedulerClient,
    attempt: WorkflowAttempt,
    workflow: WorkflowInstance,
) -> dict[str, Any]:
    return {
        "state": workflow.state,
        "error": workflow.error or attempt.error,
        "tasks": live_workflow_tasks(client, attempt, workflow),
    }


def workflow_attempt_state(
    attempt: WorkflowAttempt,
    workflow: WorkflowInstance | None,
) -> str:
    if attempt.submission_state in {
        AUTO_SAVE_PENDING_STATE,
        "AUTO_SAVE_FAILED",
        WORKFLOW_RETRY_PENDING_STATE,
    }:
        return attempt.submission_state
    return workflow.state if workflow is not None else attempt.submission_state


def retry_baseline_task_instance_id(attempt: WorkflowAttempt) -> int:
    for event in reversed(attempt.events or []):
        if (
            event.get("event") == "WORKFLOW_CONTROL_REQUESTED"
            and event.get("action") == WorkflowAction.RETRY_FAILED.value
        ):
            try:
                return int(event.get("previous_task_instance_id") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def workflow_attempt_summary(
    client: DolphinSchedulerClient | None,
    attempt: WorkflowAttempt,
    workflow: WorkflowInstance | None,
    attempt_number: int,
    tasks_error: str | None = None,
) -> dict[str, Any]:
    tasks = (
        [workflow_task_summary(task) for task in live_workflow_tasks(client, attempt, workflow)]
        if client is not None and workflow is not None
        else []
    )
    updated_at = (
        max(attempt.updated_at, workflow.updated_at)
        if workflow is not None
        else attempt.updated_at
    )
    return {
        "attempt_id": attempt.id,
        "attempt_number": attempt_number,
        "is_current": attempt.is_current,
        "workflow_instance_id": workflow.workflow_instance_id if workflow is not None else None,
        "workflow_definition_code": attempt.workflow_definition_code,
        "state": workflow_attempt_state(attempt, workflow),
        "tasks": tasks,
        "tasks_error": tasks_error,
        "created_at": attempt.created_at,
        "updated_at": updated_at,
        "started_at": workflow.started_at if workflow is not None else None,
        "finished_at": workflow.finished_at if workflow is not None else None,
        "duration_seconds": workflow.duration_seconds if workflow is not None else None,
    }


def workflow_workspace_list_item(
    workspace: WorkflowWorkspace,
    username: str,
    attempt_count: int,
    current_attempt: dict[str, Any],
    project_reference: tuple[int, str] | None,
) -> dict[str, Any]:
    return {
        "application": workspace.application,
        "workspace_id": workspace.id,
        "user_id": workspace.user_id,
        "project_id": project_reference[0] if project_reference is not None else None,
        "project_title": project_reference[1] if project_reference is not None else None,
        "owner_username": username,
        "attempt_count": attempt_count,
        "current_attempt": current_attempt,
    }


def workspace_project_references(
    session: Session,
    workspaces: Sequence[WorkflowWorkspace],
) -> dict[int, tuple[int, str]]:
    workspace_ids: dict[str, list[int]] = {}
    for workspace in workspaces:
        workspace_ids.setdefault(workspace.application, []).append(workspace.id)
    statements = []
    query_ids = workspace_ids.get("query", [])
    if query_ids:
        statements.append(
            select(QueryProject.workflow_workspace_id, QueryProject.id, QueryProject.title).where(QueryProject.workflow_workspace_id.in_(query_ids))
        )
    factor_ids = workspace_ids.get("factor", [])
    if factor_ids:
        statements.append(
            select(FactorVersion.workflow_workspace_id, FactorProject.id, FactorProject.title)
            .join(FactorProject, FactorProject.id == FactorVersion.project_id)
            .where(FactorVersion.workflow_workspace_id.in_(factor_ids))
        )
    backtest_ids = workspace_ids.get("backtest", [])
    if backtest_ids:
        statements.append(
            select(BacktestVersion.workflow_workspace_id, BacktestProject.id, BacktestProject.title)
            .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
            .where(BacktestVersion.workflow_workspace_id.in_(backtest_ids))
        )
    sensitivity_ids = workspace_ids.get("sensitivity", [])
    if sensitivity_ids:
        statements.append(
            select(BacktestResearch.workflow_workspace_id, BacktestProject.id, BacktestProject.title)
            .join(BacktestVersion, BacktestVersion.id == BacktestResearch.version_id)
            .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
            .where(BacktestResearch.workflow_workspace_id.in_(sensitivity_ids))
        )
    optimization_ids = workspace_ids.get("optimization", [])
    if optimization_ids:
        statements.append(
            select(BacktestOptimization.workflow_workspace_id, BacktestProject.id, BacktestProject.title)
            .join(BacktestVersion, BacktestVersion.id == BacktestOptimization.version_id)
            .join(BacktestProject, BacktestProject.id == BacktestVersion.project_id)
            .where(BacktestOptimization.workflow_workspace_id.in_(optimization_ids))
        )
    if not statements:
        return {}
    statement = statements[0] if len(statements) == 1 else union_all(*statements)
    return {
        int(workspace_id): (int(project_id), str(title))
        for workspace_id, project_id, title in session.execute(statement)
    }


def live_workflow_tasks(
    client: DolphinSchedulerClient,
    attempt: WorkflowAttempt,
    workflow: WorkflowInstance,
) -> list[dict[str, Any]]:
    instances = client.process_instance_tasks(
        project_code=int(attempt.project_code or 0),
        process_instance_id=workflow.workflow_instance_id,
    )
    try:
        definition = workflow_definition_details(
            int(attempt.workflow_definition_code or 0)
        )
    except DolphinSchedulerError:
        definition = {}
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
        "state": str((instance or {}).get("state") or "WAITING"),
        "host": (instance or {}).get("host"),
        "duration_seconds": duration_seconds(started_at, finished_at),
    }


def workflow_task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_code": task["task_code"],
        "task_instance_id": task["task_instance_id"],
        "name": task["name"],
        "state": task["state"],
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
    workspace: WorkflowWorkspace,
    attempt: WorkflowAttempt,
    workflow: WorkflowInstance,
    scheduler_instance: dict[str, Any],
) -> None:
    if attempt.submission_state != "AUTO_SAVE_FAILED":
        attempt.error = None
    apply_workflow_state(workflow, scheduler_instance)
    if attempt.is_current and workflow.state == "SUCCESS" and auto_save_metadata(attempt) is not None and attempt.submission_state not in {AUTO_SAVE_PENDING_STATE, "AUTO_SAVE_COMPLETE"} and attempt.error is None:
        attempt.submission_state = AUTO_SAVE_PENDING_STATE
    if workflow.state not in FAILURE_STATES:
        workflow.error = None
        return
    tasks = client.process_instance_tasks(
        project_code=int(attempt.project_code or 0),
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


def record_event(attempt: WorkflowAttempt, event: str, **details: Any) -> None:
    attempt.events = [
        *(attempt.events or []),
        {"event": event, "timestamp": utc_now().isoformat(), **details},
    ]


def auto_save_metadata(attempt: WorkflowAttempt) -> dict[str, Any] | None:
    for event in reversed(attempt.events or []):
        if event.get("event") == "AUTO_SAVE_VERSION":
            return event
    return None


def attempt_context_events(attempt: WorkflowAttempt) -> list[dict[str, Any]]:
    return [dict(event) for event in attempt.events or [] if event.get("event") in ATTEMPT_CONTEXT_EVENTS]


def auto_save_workspaces(
    session: Session,
    version_model: type[FactorVersion] | type[BacktestVersion],
    user_id: int,
    project_id: int,
    client_ids: set[str],
) -> tuple[dict[str, int], list[int], list[int]]:
    workspaces = session.scalars(
        select(WorkflowWorkspace)
        .join(version_model, version_model.workflow_workspace_id == WorkflowWorkspace.id)
        .where(WorkflowWorkspace.user_id == user_id, version_model.project_id == project_id)
    )
    workspace_ids_by_client: dict[str, int] = {}
    submission_retry_ids: list[int] = []
    auto_save_retry_ids: list[int] = []
    for workspace in workspaces:
        attempt = current_workflow_attempt(session, workspace.id)
        metadata = auto_save_metadata(attempt) if attempt is not None else None
        client_id = metadata.get("client_id") if metadata is not None else None
        if isinstance(client_id, str) and client_id in client_ids:
            workflow = current_workflow_instance(session, workspace.id)
            submission_failed = attempt.submission_state == "SUBMIT_FAILED"
            submission_unreconciled = (
                workflow is None
                and attempt.submission_state == "SUBMITTED"
            )
            execution_failed = (
                workflow is not None
                and workflow.state in FAILURE_STATES
                and attempt.submission_state not in SUBMISSION_ACTIVE_STATES
            )
            if submission_failed or submission_unreconciled or execution_failed:
                recover_ambiguous_submission = (
                    submission_failed or submission_unreconciled
                )
                retry = create_workflow_attempt(
                    session,
                    workspace,
                    attempt.input_json,
                    attempt.requested_outputs,
                    start_parameters=(
                        attempt.start_parameters
                        if recover_ambiguous_submission
                        else None
                    ),
                    project_code=(
                        attempt.project_code
                        if recover_ambiguous_submission
                        else None
                    ),
                    workflow_definition_code=(
                        attempt.workflow_definition_code
                        if recover_ambiguous_submission
                        else None
                    ),
                    workflow_name=(
                        attempt.workflow_name
                        if recover_ambiguous_submission
                        else None
                    ),
                    submission_state=BATCH_PENDING_STATE,
                    events=attempt_context_events(attempt),
                )
                record_event(
                    retry,
                    "WORKFLOW_RETRY_QUEUED",
                    previous_attempt_id=attempt.id,
                    previous_workflow_instance_id=(
                        workflow.workflow_instance_id
                        if workflow is not None
                        else None
                    ),
                )
                submission_retry_ids.append(workspace.id)
            elif attempt.submission_state == "AUTO_SAVE_FAILED":
                attempt.submission_state = AUTO_SAVE_PENDING_STATE
                attempt.error = None
                record_event(attempt, "AUTO_VERSION_SAVE_RETRY_QUEUED")
                auto_save_retry_ids.append(workspace.id)
            workspace_ids_by_client[client_id] = workspace.id
    return workspace_ids_by_client, submission_retry_ids, auto_save_retry_ids


def submit_attempts_now(attempt_ids: Sequence[int]) -> None:
    def submit(attempt_id: int) -> None:
        with database_session_factory()() as session:
            row = session.execute(
                select(WorkflowAttempt, WorkflowWorkspace)
                .join(WorkflowWorkspace, WorkflowWorkspace.id == WorkflowAttempt.workflow_workspace_id)
                .where(
                    WorkflowAttempt.id == attempt_id,
                    WorkflowAttempt.is_current.is_(True),
                    WorkflowAttempt.submission_state == BATCH_PENDING_STATE,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                return
            attempt, workspace = row
            try:
                executor = WorkflowExecutionService(workspace.application)
                executor.submit_workspace(
                    session,
                    workspace,
                    create_directory=not workspace_directory(workspace.application, workspace.workspace_key).exists(),
                    wait_for_workflow=False,
                )
                finalize_project_auto_save_workspace(session, workspace)
            except (DolphinSchedulerError, OSError, RuntimeError, ValueError) as error:
                LOGGER.warning("批量提交 %s workflow attempt %s 失败: %s", workspace.application, attempt_id, error)
            except Exception as error:
                session.rollback()
                failed = session.get(WorkflowAttempt, attempt_id)
                if failed is not None:
                    failed.submission_state = "SUBMIT_FAILED"
                    failed.error = str(error)
                    record_event(failed, "WORKFLOW_SUBMIT_FAILED", error=str(error))
                    session.commit()
                LOGGER.exception("批量提交 workflow attempt %s 失败", attempt_id)

    if not attempt_ids:
        return
    with ThreadPoolExecutor() as pool:
        list(pool.map(submit, attempt_ids))


def submit_workspaces_now(workspace_ids: Sequence[int]) -> None:
    if not workspace_ids:
        return
    with database_session_factory()() as session:
        attempt_ids = list(
            session.scalars(
                select(WorkflowAttempt.id).where(
                    WorkflowAttempt.workflow_workspace_id.in_(workspace_ids),
                    WorkflowAttempt.is_current.is_(True),
                )
            )
        )
    submit_attempts_now(attempt_ids)


def finalize_auto_save_workspaces_now(workspace_ids: Sequence[int]) -> None:
    for workspace_id in workspace_ids:
        with database_session_factory()() as session:
            workspace = session.get(WorkflowWorkspace, workspace_id)
            if workspace is None:
                continue
            try:
                finalize_project_auto_save_workspace(session, workspace)
            except Exception as error:
                LOGGER.warning("重试自动保存 %s workspace %s 失败: %s", workspace.application, workspace.id, error)


def workflow_marker(workspace: WorkflowWorkspace, attempt: WorkflowAttempt) -> str:
    existing = attempt.start_parameters.get("job_id")
    return existing or f"{workspace.application}:{workspace.id}:{attempt.id}"


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
    root_error = ""
    for _ in range(100):
        try:
            page = client.task_log(
                task_instance_id=task_instance_id,
                skip_line_num=skip_line_num,
                limit=1000,
            )
        except DolphinSchedulerError:
            return f"DolphinScheduler task state: {state}"
        extracted = extract_root_error(page["message"])
        if extracted:
            root_error = extracted
        if not page["has_more"] or page["next_line_num"] == skip_line_num:
            break
        skip_line_num = page["next_line_num"]
    return root_error or f"DolphinScheduler task state: {state}; call the task log API for the complete log"


def extract_root_error(log: str) -> str:
    """从完整任务日志提取根异常摘要；完整日志仍由分页日志接口保留。"""
    server_responses = re.findall(
        r"Server Response:\s*['\"](.*?)(?:['\"]\s+script:|['\"]\s*$)",
        log,
        flags=re.DOTALL,
    )
    if server_responses:
        return server_responses[-1].strip()
    exceptions = re.findall(
        r"(?m)^\s*(?:RuntimeError|ValueError|TypeError|KeyError|FileNotFoundError|Exception):\s*(.+)$",
        log,
    )
    return exceptions[-1].strip() if exceptions else ""


def resolve_workspace_directory(workspace: WorkflowWorkspace) -> Path:
    return workspace_directory(workspace.application, workspace.workspace_key)


def resolve_workspace_artifacts(workspace: WorkflowWorkspace) -> tuple[Path, str, str]:
    """返回经过应用类型校验的一次工作流产物目录。"""
    return resolve_workspace_directory(workspace), workspace.application, workspace.workspace_key


def remove_workspace_artifacts(
    run_directory: Path,
    application: str,
    workspace_key: str,
) -> None:
    """清理一次工作流的本地输入目录及可选云端结果目录。"""
    delete_result_objects(application, workspace_key)
    if run_directory.exists():
        shutil.rmtree(run_directory)
