"""Small DolphinScheduler 3.4.x HTTP client."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Self

import httpx

from scheduler.config import DolphinSchedulerSettings


class DolphinSchedulerError(RuntimeError):
    """Raised when DolphinScheduler rejects a request."""


class DolphinSchedulerClient:
    """Authenticated client for the DolphinScheduler endpoints used here."""

    def __init__(
        self,
        settings: DolphinSchedulerSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._session_id: str | None = None
        self._client = httpx.Client(
            base_url=settings.base_url,
            timeout=settings.request_timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def login(self) -> None:
        response = self._client.post(
            "/login",
            params={
                "userName": self.settings.username,
                "userPassword": self.settings.password,
            },
        )
        data = self._decode_response(response, "登录")
        if not isinstance(data, Mapping) or not data.get("sessionId"):
            raise DolphinSchedulerError("DolphinScheduler 登录结果缺少 sessionId")
        self._session_id = str(data["sessionId"])

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        data: Mapping[str, object] | None = None,
    ) -> Any:
        if self._session_id is None:
            self.login()
        response = self._client.request(
            method,
            path,
            params=params,
            data=data,
            headers={"sessionId": self._session_id or ""},
        )
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self.login()
            response = self._client.request(
                method,
                path,
                params=params,
                data=data,
                headers={"sessionId": self._session_id or ""},
            )
        return self._decode_response(response, f"{method} {path}")

    @staticmethod
    def _decode_response(response: httpx.Response, action: str) -> Any:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise DolphinSchedulerError(
                f"DolphinScheduler {action}失败：HTTP "
                f"{response.status_code} {response.text}",
            ) from error
        try:
            body = response.json()
        except ValueError as error:
            raise DolphinSchedulerError(
                f"DolphinScheduler {action}返回了非 JSON 响应",
            ) from error
        if not isinstance(body, Mapping):
            raise DolphinSchedulerError(
                f"DolphinScheduler {action}返回格式错误：{body!r}",
            )
        if body.get("code") != 0:
            message = body.get("msg") or body.get("message") or body
            raise DolphinSchedulerError(
                f"DolphinScheduler {action}失败：{message}",
            )
        return body.get("data")

    @staticmethod
    def page_items(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
            return [dict(item) for item in data if isinstance(item, Mapping)]
        if not isinstance(data, Mapping):
            return []
        for key in ("totalList", "data", "records"):
            items = data.get(key)
            if isinstance(items, Sequence) and not isinstance(
                items,
                (str, bytes),
            ):
                return [dict(item) for item in items if isinstance(item, Mapping)]
        return []

    def find_project(self, name: str) -> dict[str, Any] | None:
        data = self.request(
            "GET",
            "/projects",
            params={"pageNo": 1, "pageSize": 100, "searchVal": name},
        )
        return next(
            (item for item in self.page_items(data) if item.get("name") == name),
            None,
        )

    def create_project(self, name: str, description: str) -> dict[str, Any]:
        data = self.request(
            "POST",
            "/projects",
            params={"projectName": name, "description": description},
        )
        if not isinstance(data, Mapping):
            raise DolphinSchedulerError("创建 Project 的结果格式错误")
        return dict(data)

    def list_task_groups(self, project_code: int) -> list[dict[str, Any]]:
        data = self.request(
            "GET",
            "/task-group/query-list-by-projectCode",
            params={
                "pageNo": 1,
                "pageSize": 100,
                "projectCode": str(project_code),
            },
        )
        return self.page_items(data)

    def create_task_group(
        self,
        *,
        project_code: int,
        name: str,
        description: str,
        group_size: int,
    ) -> dict[str, Any]:
        data = self.request(
            "POST",
            "/task-group/create",
            params={
                "projectCode": project_code,
                "name": name,
                "description": description,
                "groupSize": group_size,
            },
        )
        if not isinstance(data, Mapping):
            raise DolphinSchedulerError("创建 Task Group 的结果格式错误")
        return dict(data)

    def update_task_group(
        self,
        *,
        task_group_id: int,
        name: str,
        description: str,
        group_size: int,
    ) -> dict[str, Any]:
        data = self.request(
            "POST",
            "/task-group/update",
            params={
                "id": task_group_id,
                "name": name,
                "description": description,
                "groupSize": group_size,
            },
        )
        if not isinstance(data, Mapping):
            raise DolphinSchedulerError("更新 Task Group 的结果格式错误")
        return dict(data)

    def find_workflow(
        self,
        project_code: int,
        name: str,
    ) -> dict[str, Any] | None:
        data = self.request(
            "GET",
            f"/projects/{project_code}/workflow-definition",
            params={"pageNo": 1, "pageSize": 100, "searchVal": name},
        )
        return next(
            (item for item in self.page_items(data) if item.get("name") == name),
            None,
        )

    def get_workflow(self, project_code: int, code: int) -> dict[str, Any]:
        data = self.request(
            "GET",
            f"/projects/{project_code}/workflow-definition/{code}",
        )
        if not isinstance(data, Mapping):
            raise DolphinSchedulerError("查询 Workflow 的结果格式错误")
        return dict(data)

    def generate_task_codes(
        self,
        project_code: int,
        count: int,
    ) -> list[int]:
        data = self.request(
            "GET",
            f"/projects/{project_code}/task-definition/gen-task-codes",
            params={"genNum": count},
        )
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            raise DolphinSchedulerError("生成 Task Code 的结果格式错误")
        codes = [int(code) for code in data]
        if len(codes) != count:
            raise DolphinSchedulerError(
                f"请求 {count} 个 Task Code，只返回了 {len(codes)} 个",
            )
        return codes

    def create_workflow(
        self,
        project_code: int,
        params: Mapping[str, object],
    ) -> dict[str, Any]:
        data = self.request(
            "POST",
            f"/projects/{project_code}/workflow-definition",
            data=params,
        )
        if not isinstance(data, Mapping):
            raise DolphinSchedulerError("创建 Workflow 的结果格式错误")
        return dict(data)

    def update_workflow(
        self,
        project_code: int,
        code: int,
        params: Mapping[str, object],
    ) -> dict[str, Any]:
        data = self.request(
            "PUT",
            f"/projects/{project_code}/workflow-definition/{code}",
            data=params,
        )
        if not isinstance(data, Mapping):
            raise DolphinSchedulerError("更新 Workflow 的结果格式错误")
        return dict(data)

    def release_workflow(
        self,
        project_code: int,
        code: int,
        release_state: str = "ONLINE",
    ) -> None:
        self.request(
            "POST",
            f"/projects/{project_code}/workflow-definition/{code}/release",
            params={"releaseState": release_state},
        )

    def start_workflow(self, project_code: int, code: int) -> tuple[int, ...]:
        data = self.request(
            "POST",
            f"/projects/{project_code}/executors/start-workflow-instance",
            params={
                "workflowDefinitionCode": code,
                "scheduleTime": "",
                "failureStrategy": "CONTINUE",
                "execType": "START_PROCESS",
                "warningType": "NONE",
                "workflowInstancePriority": "MEDIUM",
                "workerGroup": self.settings.worker_group,
                "tenantCode": self.settings.tenant_code,
                "dryRun": 0,
            },
        )
        if data is None:
            return ()
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            raise DolphinSchedulerError("启动 Workflow 的结果格式错误")
        return tuple(int(item) for item in data)
