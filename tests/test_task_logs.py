"""Task log scope and pagination coverage."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.apps.tasks.schemas import TaskLogResponse, TaskLogScope
from core.apps.tasks.services import append_worker_output_lines, worker_task_log_page
from core.apps.tasks.views import router
from core.apps.users.services import get_current_user
from core.database.session import get_database_session


class PagedLogClient:
    def __init__(self, pages: dict[int, dict[str, object]]) -> None:
        self.pages = pages
        self.requests: list[tuple[int, int]] = []

    def task_log(
        self,
        *,
        task_instance_id: int,
        skip_line_num: int,
        limit: int,
    ) -> dict[str, object]:
        assert task_instance_id == 42
        self.requests.append((skip_line_num, limit))
        return self.pages[skip_line_num]


def test_worker_log_page_uses_a_worker_scoped_cursor_across_raw_pages() -> None:
    client = PagedLogClient({
        0: {
            "message": "\n".join([
                "[INFO] 2026-08-30 10:00:00.001 +0800 - prepare task",
                "[INFO] 2026-08-30 10:00:00.002 +0800 -  -> ",
                "\tfirst",
                "\tsecond",
                "[INFO] 2026-08-30 10:00:00.003 +0800 - process running",
            ]),
            "next_line_num": 5,
            "has_more": True,
        },
        5: {
            "message": "\n".join([
                "[INFO] 2026-08-30 10:00:01.001 +0800 - -> third",
                "\tfourth",
                "[INFO] 2026-08-30 10:00:01.002 +0800 - process exited",
            ]),
            "next_line_num": 8,
            "has_more": False,
        },
    })

    first = worker_task_log_page(
        client, task_instance_id=42, skip_line_num=0, limit=2
    )
    second = worker_task_log_page(
        client,
        task_instance_id=42,
        skip_line_num=first["next_line_num"],
        limit=2,
        cursor=first["next_cursor"],
    )

    assert {key: first[key] for key in (
        "skip_line_num", "returned_lines", "next_line_num", "has_more", "message"
    )} == {
        "skip_line_num": 0,
        "returned_lines": 2,
        "next_line_num": 2,
        "has_more": True,
        "message": "first\nsecond",
    }
    assert {key: second[key] for key in (
        "skip_line_num", "returned_lines", "next_line_num", "has_more", "message"
    )} == {
        "skip_line_num": 2,
        "returned_lines": 2,
        "next_line_num": 4,
        "has_more": False,
        "message": "third\nfourth",
    }
    # The first page probes the next raw page once to make has_more exact;
    # the following request resumes from that raw page instead of rescanning 0.
    assert client.requests == [(0, 10_000), (5, 10_000), (5, 10_000)]


def test_worker_output_accepts_a_dolphinscheduler_logger_source() -> None:
    output: list[str] = []

    inside = append_worker_output_lines(
        output,
        (
            "[INFO] 2026-08-30 10:00:00 +0800 "
            "org.apache.dolphinscheduler.server.worker.runner.TaskExecuteRunnable - ->\n"
            "\tworker value"
        ),
        inside_worker_output=False,
    )

    assert output == ["worker value"]
    assert inside is True


def test_worker_output_entry_state_carries_to_the_next_raw_page() -> None:
    output: list[str] = []
    inside = append_worker_output_lines(
        output,
        "[INFO] 2026-08-30 10:00:00 +0800 - ->\n\tline one",
        inside_worker_output=False,
    )
    inside = append_worker_output_lines(
        output,
        "\tline two\n[INFO] 2026-08-30 10:00:01 +0800 - process exited",
        inside_worker_output=inside,
    )

    assert output == ["line one", "line two"]
    assert inside is False


def test_worker_log_page_preserves_a_final_blank_output_line() -> None:
    client = PagedLogClient({
        0: {
            "message": "[INFO] 2026-08-30 10:00:00 +0800 - ->\n\tvalue\n\t",
            "next_line_num": 3,
            "has_more": False,
        }
    })

    page = worker_task_log_page(
        client, task_instance_id=42, skip_line_num=0, limit=2
    )

    assert page["returned_lines"] == 2
    assert page["message"] == "value\n\n"


def test_worker_log_page_returns_an_empty_stable_cursor_before_output_exists() -> None:
    client = PagedLogClient({
        0: {
            "message": "[INFO] 2026-08-30 10:00:00 +0800 - prepare task",
            "next_line_num": 1,
            "has_more": False,
        }
    })

    page = worker_task_log_page(
        client, task_instance_id=42, skip_line_num=0, limit=100
    )

    assert page["message"] == ""
    assert page["returned_lines"] == page["next_line_num"] == 0
    assert page["has_more"] is False


def test_task_log_response_keeps_full_as_the_backward_compatible_default() -> None:
    response = TaskLogResponse.model_validate({
        "workflow_instance_id": 1,
        "task_instance_id": 2,
        "state": "SUCCESS",
        "skip_line_num": 0,
        "returned_lines": 0,
        "next_line_num": 0,
        "has_more": False,
        "message": "",
    })

    assert response.scope is TaskLogScope.FULL


def test_task_log_http_api_passes_worker_scope_to_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[TaskLogScope, object]] = []

    class Gateway:
        def log(self, *args: object) -> dict[str, object]:
            scope = args[-2]
            assert isinstance(scope, TaskLogScope)
            captured.append((scope, args[-1]))
            return {
                "workflow_instance_id": 1,
                "task_instance_id": 2,
                "state": "RUNNING_EXECUTION",
                "scope": scope,
                "skip_line_num": 0,
                "returned_lines": 1,
                "next_line_num": 1,
                "has_more": False,
                "message": "worker line",
                "next_cursor": "cursor",
            }

    monkeypatch.setattr("core.apps.tasks.views.TaskGatewayService", Gateway)
    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_current_user] = lambda: object()
    application.dependency_overrides[get_database_session] = lambda: object()
    client = TestClient(application)

    response = client.get(
        "/api/v1/tasks/2/logs",
        params={
            "workflow_instance_id": 1,
            "scope": "worker",
            "cursor": "opaque-cursor",
        },
    )

    assert response.status_code == 200
    assert response.json()["scope"] == "worker"
    assert captured == [(TaskLogScope.WORKER, "opaque-cursor")]
    assert response.json()["next_cursor"] == "cursor"
    assert client.get(
        "/api/v1/tasks/2/logs",
        params={"workflow_instance_id": 1, "scope": "scheduler"},
    ).status_code == 422
