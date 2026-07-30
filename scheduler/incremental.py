"""Incremental data update workflow definition."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from scheduler.client import DolphinSchedulerClient, DolphinSchedulerError
from scheduler.config import DolphinSchedulerSettings

INCREMENTAL_WORKERS = (
    "daily",
    "fund-daily",
    "fund-adj-factor",
    "limit",
    "daily-basic",
    "adj-factor",
    "hfq",
    "st",
    "balance-sheet",
    "income",
    "cashflow",
    "fina-indicator",
    "dividend",
    "index-weight",
)


@dataclass(frozen=True, slots=True)
class IncrementalTask:
    """One independently scheduled Runtime Worker."""

    worker_name: str
    task_group_id: int
    task_code: int
    command: str


@dataclass(frozen=True, slots=True)
class IncrementalWorkflow:
    """Provisioned workflow identifiers."""

    project_code: int
    workflow_code: int
    workflow_version: int
    workflow_created: bool
    workflow_updated: bool
    task_count: int


@dataclass(frozen=True, slots=True)
class IncrementalUpdateSubmission:
    """Result returned after the workflow is sent for execution."""

    project_code: int
    workflow_code: int
    workflow_version: int
    workflow_instance_ids: tuple[int, ...]
    task_count: int


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _as_integer(value: object, *, location: str) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise DolphinSchedulerError(
            f"{location} 缺少有效整数：{value!r}",
        ) from error


def _ensure_project(
    client: DolphinSchedulerClient,
    settings: DolphinSchedulerSettings,
) -> dict[str, Any]:
    project = client.find_project(settings.project_name)
    if project is not None:
        return project
    return client.create_project(
        settings.project_name,
        "Arena Runtime 调度任务",
    )


def _ensure_task_group(
    client: DolphinSchedulerClient,
    settings: DolphinSchedulerSettings,
    project_code: int,
) -> int:
    existing = {
        str(item.get("name")): item for item in client.list_task_groups(project_code)
    }
    group_name = settings.task_group_prefix
    description = "Arena Runtime 增量更新全局限速组"
    task_group = existing.get(group_name)
    if task_group is None:
        task_group = client.create_task_group(
            project_code=project_code,
            name=group_name,
            description=description,
            group_size=1,
        )
    elif (
        _as_integer(
            task_group.get("groupSize"),
            location=f"Task Group {group_name} groupSize",
        )
        != 1
    ):
        task_group = client.update_task_group(
            task_group_id=_as_integer(
                task_group.get("id"),
                location=f"Task Group {group_name} id",
            ),
            name=group_name,
            description=description,
            group_size=1,
        )
    return _as_integer(
        task_group.get("id"),
        location=f"Task Group {group_name} id",
    )


def _existing_task_codes(
    dag: Mapping[str, Any] | None,
) -> dict[str, int]:
    if dag is None:
        return {}
    definitions = dag.get("taskDefinitionList")
    if not isinstance(definitions, list):
        return {}
    result: dict[str, int] = {}
    for definition in definitions:
        if not isinstance(definition, Mapping):
            continue
        name = definition.get("name")
        code = definition.get("code")
        if isinstance(name, str) and code is not None:
            result[name] = _as_integer(
                code,
                location=f"Task {name} code",
            )
    return result


def _build_tasks(
    client: DolphinSchedulerClient,
    settings: DolphinSchedulerSettings,
    project_code: int,
    task_group_id: int,
    existing_codes: Mapping[str, int],
) -> tuple[IncrementalTask, ...]:
    missing_names = [name for name in INCREMENTAL_WORKERS if name not in existing_codes]
    generated_codes = iter(
        client.generate_task_codes(project_code, len(missing_names))
        if missing_names
        else ()
    )
    tasks: list[IncrementalTask] = []
    for worker_name in INCREMENTAL_WORKERS:
        task_code = existing_codes.get(worker_name)
        if task_code is None:
            task_code = next(generated_codes)
        tasks.append(
            IncrementalTask(
                worker_name=worker_name,
                task_group_id=task_group_id,
                task_code=task_code,
                command=(
                    f"exec {settings.runtime_command} workers {worker_name} "
                    f"--threads {settings.incremental_threads} "
                    f"--throttle {settings.incremental_throttle}"
                ),
            ),
        )
    return tuple(tasks)


def build_workflow_parameters(
    settings: DolphinSchedulerSettings,
    tasks: tuple[IncrementalTask, ...],
) -> dict[str, object]:
    """Build the exact DolphinScheduler 3.4 workflow request parameters."""
    definitions = []
    relations = []
    locations = []
    for index, task in enumerate(tasks):
        definitions.append(
            {
                "code": task.task_code,
                "delayTime": "0",
                "description": f"{task.worker_name} 增量更新",
                "environmentCode": -1,
                "failRetryInterval": "1",
                "failRetryTimes": "1",
                "flag": "YES",
                "name": task.worker_name,
                "taskGroupId": task.task_group_id,
                "taskGroupPriority": 0,
                "taskParams": {
                    "localParams": [],
                    "rawScript": task.command,
                    "resourceList": [],
                },
                "taskPriority": "MEDIUM",
                "taskType": "SHELL",
                "timeout": 0,
                "timeoutFlag": "CLOSE",
                "timeoutNotifyStrategy": "",
                "workerGroup": settings.worker_group,
                "cpuQuota": -1,
                "memoryMax": -1,
                "taskExecuteType": "BATCH",
            },
        )
        relations.append(
            {
                "name": "",
                "preTaskCode": 0,
                "preTaskVersion": 0,
                "postTaskCode": task.task_code,
                "postTaskVersion": 0,
                "conditionType": "NONE",
                "conditionParams": {},
            },
        )
        locations.append(
            {
                "taskCode": task.task_code,
                "x": 160 + (index % 4) * 280,
                "y": 120 + (index // 4) * 180,
            },
        )
    return {
        "name": settings.workflow_name,
        "description": "Arena Runtime 全量增量更新任务",
        "globalParams": "[]",
        "locations": _json(locations),
        "timeout": 0,
        "taskRelationJson": _json(relations),
        "taskDefinitionJson": _json(definitions),
        "executionType": "PARALLEL",
    }


def _parse_task_params(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        if isinstance(parsed, Mapping):
            return parsed
    return {}


def _workflow_matches(
    dag: Mapping[str, Any],
    tasks: tuple[IncrementalTask, ...],
    settings: DolphinSchedulerSettings,
) -> bool:
    definitions = dag.get("taskDefinitionList")
    relations = dag.get("workflowTaskRelationList")
    workflow = dag.get("workflowDefinition")
    if (
        not isinstance(definitions, list)
        or not isinstance(relations, list)
        or not isinstance(workflow, Mapping)
        or workflow.get("executionType") != "PARALLEL"
    ):
        return False
    definitions_by_name = {
        str(item.get("name")): item for item in definitions if isinstance(item, Mapping)
    }
    if set(definitions_by_name) != set(INCREMENTAL_WORKERS):
        return False
    for task in tasks:
        current = definitions_by_name[task.worker_name]
        params = _parse_task_params(current.get("taskParams"))
        checks = (
            current.get("taskType") == "SHELL",
            current.get("workerGroup") == settings.worker_group,
            _as_integer(
                current.get("taskGroupId"),
                location=f"Task {task.worker_name} taskGroupId",
            )
            == task.task_group_id,
            str(current.get("failRetryTimes")) == "1",
            str(current.get("failRetryInterval")) == "1",
            params.get("rawScript") == task.command,
        )
        if not all(checks):
            return False
    expected_relations = {(0, task.task_code) for task in tasks}
    current_relations = {
        (
            _as_integer(item.get("preTaskCode"), location="preTaskCode"),
            _as_integer(item.get("postTaskCode"), location="postTaskCode"),
        )
        for item in relations
        if isinstance(item, Mapping)
    }
    return current_relations == expected_relations


def ensure_incremental_update_workflow(
    settings: DolphinSchedulerSettings | None = None,
    *,
    client: DolphinSchedulerClient | None = None,
) -> IncrementalWorkflow:
    """Idempotently create/update and publish the incremental workflow."""
    current_settings = settings or DolphinSchedulerSettings.from_environment()
    owns_client = client is None
    current_client = client or DolphinSchedulerClient(current_settings)
    try:
        project = _ensure_project(current_client, current_settings)
        project_code = _as_integer(
            project.get("code"),
            location=f"Project {current_settings.project_name} code",
        )
        task_group_id = _ensure_task_group(
            current_client,
            current_settings,
            project_code,
        )
        workflow = current_client.find_workflow(
            project_code,
            current_settings.workflow_name,
        )
        dag = None
        if workflow is not None:
            dag = current_client.get_workflow(
                project_code,
                _as_integer(
                    workflow.get("code"),
                    location="Workflow code",
                ),
            )
        tasks = _build_tasks(
            current_client,
            current_settings,
            project_code,
            task_group_id,
            _existing_task_codes(dag),
        )
        parameters = build_workflow_parameters(current_settings, tasks)
        workflow_created = workflow is None
        workflow_updated = False
        if workflow is None:
            workflow = current_client.create_workflow(
                project_code,
                parameters,
            )
        elif dag is not None and not _workflow_matches(
            dag,
            tasks,
            current_settings,
        ):
            workflow_updated = True
            workflow_code = _as_integer(
                workflow.get("code"),
                location="Workflow code",
            )
            current_client.release_workflow(
                project_code,
                workflow_code,
                "OFFLINE",
            )
            parameters["releaseState"] = "OFFLINE"
            workflow = current_client.update_workflow(
                project_code,
                workflow_code,
                parameters,
            )
        workflow_code = _as_integer(
            workflow.get("code"),
            location="Workflow code",
        )
        current_client.release_workflow(project_code, workflow_code)
        refreshed = current_client.get_workflow(project_code, workflow_code)
        definition = refreshed.get("workflowDefinition")
        if not isinstance(definition, Mapping):
            raise DolphinSchedulerError("Workflow 详情缺少 workflowDefinition")
        return IncrementalWorkflow(
            project_code=project_code,
            workflow_code=workflow_code,
            workflow_version=_as_integer(
                definition.get("version"),
                location="Workflow version",
            ),
            workflow_created=workflow_created,
            workflow_updated=workflow_updated,
            task_count=len(tasks),
        )
    finally:
        if owns_client:
            current_client.close()


def create_and_submit_incremental_update(
    settings: DolphinSchedulerSettings | None = None,
    *,
    client: DolphinSchedulerClient | None = None,
) -> IncrementalUpdateSubmission:
    """Create/update the definitions, publish them, and start one run."""
    current_settings = settings or DolphinSchedulerSettings.from_environment()
    owns_client = client is None
    current_client = client or DolphinSchedulerClient(current_settings)
    try:
        workflow = ensure_incremental_update_workflow(
            current_settings,
            client=current_client,
        )
        workflow_instance_ids = current_client.start_workflow(
            workflow.project_code,
            workflow.workflow_code,
        )
        return IncrementalUpdateSubmission(
            project_code=workflow.project_code,
            workflow_code=workflow.workflow_code,
            workflow_version=workflow.workflow_version,
            workflow_instance_ids=workflow_instance_ids,
            task_count=workflow.task_count,
        )
    finally:
        if owns_client:
            current_client.close()
