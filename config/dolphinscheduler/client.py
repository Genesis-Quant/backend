"""Authenticated DolphinScheduler 3.2 HTTP API client."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.dolphinscheduler.errors import DolphinSchedulerError
from config.settings import DolphinSchedulerSettings

FILENAME_PATTERN = re.compile(r'filename="?([^";]+)"?', re.IGNORECASE)


@dataclass
class StreamedLog:
    """Streaming DolphinScheduler log response and its owning HTTP session."""

    filename: str
    content_type: str
    response: requests.Response
    session: requests.Session

    def chunks(self):
        try:
            yield from self.response.iter_content(chunk_size=64 * 1024)
        finally:
            self.response.close()
            self.session.close()


class DolphinSchedulerClient:
    """Encapsulate the DolphinScheduler endpoints used by Arena."""

    def __init__(
        self,
        settings: DolphinSchedulerSettings,
        *,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self.timeout = timeout
        self.session = session or self.create_session()
        self.owns_session = session is None
        self.logged_in = False

    def __enter__(self) -> Self:
        self.login()
        return self

    def __exit__(self, *ignored: object) -> None:
        if self.owns_session:
            self.session.close()

    @staticmethod
    def create_session() -> requests.Session:
        session = requests.Session()
        retries = Retry(
            total=2,
            connect=2,
            read=2,
            status=2,
            allowed_methods=frozenset({"GET"}),
            status_forcelist=(502, 503, 504),
            backoff_factor=0.2,
        )
        session.mount("http://", HTTPAdapter(max_retries=retries))
        session.mount("https://", HTTPAdapter(max_retries=retries))
        return session

    def login(self) -> None:
        """Authenticate the session and retain the server cookie."""
        self.request(
            "POST",
            "/login",
            params={
                "userName": self.settings.username,
                "userPassword": self.settings.password,
            },
            require_login=False,
        )
        self.logged_in = True

    def project_code(self, project_name: str) -> int | None:
        """Return a project code by name."""
        projects = self.request("GET", "/projects/list")
        for project in projects or []:
            if project.get("name") == project_name:
                return int(project["code"])
        return None

    def process_definition(
        self,
        project_code: int,
        workflow_name: str,
    ) -> dict[str, Any] | None:
        """Return the latest workflow definition with the requested name."""
        result = self.request(
            "GET",
            f"/projects/{project_code}/process-definition/query-by-name",
            params={"name": workflow_name},
            allow_empty=True,
        )
        if not result:
            return None
        return result.get("processDefinition", result)

    def start_process_instance(
        self,
        *,
        project_code: int,
        process_definition_code: int,
        start_params: dict[str, str],
    ) -> Any:
        """Start an online process definition immediately."""
        return self.request(
            "POST",
            f"/projects/{project_code}/executors/start-process-instance",
            params={
                "processDefinitionCode": process_definition_code,
                "scheduleTime": "",
                "failureStrategy": "END",
                "warningType": "NONE",
                "processInstancePriority": "MEDIUM",
                "workerGroup": self.settings.worker_group,
                "tenantCode": self.settings.tenant_code,
                "startParams": json.dumps(start_params, ensure_ascii=False),
            },
        )

    def execute_process_instance(self, project_code: int, process_instance_id: int, execute_type: str) -> Any:
        return self.request(
            "POST",
            f"/projects/{project_code}/executors/execute",
            params={"processInstanceId": process_instance_id, "executeType": execute_type},
        )

    def execute_task_instance(self, project_code: int, task_instance_id: int, action: str) -> Any:
        if action not in {"stop", "force-success"}:
            raise ValueError(f"不支持的 task instance 操作: {action}")
        return self.request("POST", f"/projects/{project_code}/task-instances/{task_instance_id}/{action}")

    def process_instance(
        self,
        project_code: int,
        process_instance_id: int,
    ) -> dict[str, Any]:
        """Return one process instance."""
        return self.request(
            "GET",
            f"/projects/{project_code}/process-instances/{process_instance_id}",
        )

    def process_instances(
        self,
        *,
        project_code: int,
        process_definition_code: int | None = None,
        page_no: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Return one page of process instances."""
        params: dict[str, Any] = {
            "pageNo": page_no,
            "pageSize": page_size,
        }
        if process_definition_code is not None:
            params["processDefineCode"] = process_definition_code
        page = self.request(
            "GET",
            f"/projects/{project_code}/process-instances",
            params=params,
        )
        return list((page or {}).get("totalList") or [])

    def process_instance_tasks(
        self,
        *,
        project_code: int,
        process_instance_id: int,
    ) -> list[dict[str, Any]]:
        """Return every task instance belonging to a process instance."""
        result = self.request(
            "GET",
            (
                f"/projects/{project_code}/process-instances/"
                f"{process_instance_id}/tasks"
            ),
        )
        return list((result or {}).get("taskList") or [])

    def task_groups(
        self,
        *,
        project_code: int,
        page_no: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Return one page of task groups belonging to a project."""
        page = self.request(
            "GET",
            "/task-group/query-list-by-projectCode",
            params={
                "projectCode": project_code,
                "pageNo": page_no,
                "pageSize": page_size,
            },
        )
        return list((page or {}).get("totalList") or [])

    def create_task_group(
        self,
        *,
        project_code: int,
        name: str,
        description: str,
        group_size: int,
    ) -> Any:
        """Create a project task group."""
        return self.request(
            "POST",
            "/task-group/create",
            params={
                "projectCode": project_code,
                "name": name,
                "description": description,
                "groupSize": group_size,
            },
        )

    def update_task_group(
        self,
        *,
        task_group_id: int,
        name: str,
        description: str,
        group_size: int,
    ) -> Any:
        """Update a task group's name, description, and capacity."""
        return self.request(
            "POST",
            "/task-group/update",
            params={
                "id": task_group_id,
                "name": name,
                "description": description,
                "groupSize": group_size,
            },
        )

    def task_log(
        self,
        *,
        task_instance_id: int,
        skip_line_num: int = 0,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Read a page of a task log and return the next line cursor."""
        result = self.request(
            "GET",
            "/log/detail",
            params={
                "taskInstanceId": task_instance_id,
                "skipLineNum": skip_line_num,
                "limit": limit,
            },
        )
        if isinstance(result, dict):
            returned_lines = int(result.get("lineNum", 0))
            return {
                "skip_line_num": skip_line_num,
                "returned_lines": returned_lines,
                "next_line_num": skip_line_num + returned_lines,
                "has_more": returned_lines == limit,
                "message": str(result.get("message", "")),
            }
        message = str(result or "")
        returned_lines = len(message.splitlines())
        return {
            "skip_line_num": skip_line_num,
            "returned_lines": returned_lines,
            "next_line_num": skip_line_num + returned_lines,
            "has_more": returned_lines == limit,
            "message": message,
        }

    def stream_task_log(
        self,
        *,
        project_code: int,
        task_instance_id: int,
    ) -> StreamedLog:
        """Open a streaming response for the complete task log."""
        response = self.send(
            "GET",
            f"/log/{project_code}/download-log",
            params={"taskInstanceId": task_instance_id},
            stream=True,
        )
        content_type = response.headers.get(
            "Content-Type",
            "application/octet-stream",
        )
        if "json" in content_type.lower():
            self.raise_for_api_payload(response)
        disposition = response.headers.get("Content-Disposition", "")
        match = FILENAME_PATTERN.search(disposition)
        filename = Path(match.group(1)).name if match else f"{task_instance_id}.log"
        return StreamedLog(
            filename=filename,
            content_type=content_type,
            response=response,
            session=self.session,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        require_login: bool = True,
        allow_empty: bool = False,
    ) -> Any:
        response = self.send(
            method,
            path,
            params=params,
            require_login=require_login,
        )
        try:
            payload = response.json()
        except ValueError as error:
            raise DolphinSchedulerError(
                f"DolphinScheduler 返回了非 JSON 响应: {method} {path}"
            ) from error
        if not payload.get("success", payload.get("code") == 0):
            if allow_empty and payload.get("data") is None:
                return None
            raise DolphinSchedulerError(
                "DolphinScheduler 拒绝请求: "
                f"{method} {path}: {payload.get('msg', payload)}"
            )
        return payload.get("data")

    def send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        require_login: bool = True,
        stream: bool = False,
    ) -> requests.Response:
        if require_login and not self.logged_in:
            self.login()
        try:
            response = self.session.request(
                method,
                f"{self.settings.api_base_url}{path}",
                params=params,
                timeout=self.timeout,
                stream=stream,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            raise DolphinSchedulerError(
                f"DolphinScheduler 请求失败: {method} {path}: {error}"
            ) from error

    @staticmethod
    def raise_for_api_payload(response: requests.Response) -> None:
        try:
            payload = response.json()
        except ValueError as error:
            raise DolphinSchedulerError(
                "DolphinScheduler 日志下载返回了无效响应"
            ) from error
        if not payload.get("success", payload.get("code") == 0):
            raise DolphinSchedulerError(
                f"DolphinScheduler 拒绝日志下载: {payload.get('msg', payload)}"
            )
