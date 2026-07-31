from types import SimpleNamespace

from scheduler.clients import DolphinSchedulerClient


class FakeResponse:
    def __init__(
        self,
        payload=None,
        *,
        content=b"",
        headers=None,
    ):
        self.payload = payload
        self.content = content
        self.headers = headers or {"Content-Type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def settings():
    return SimpleNamespace(
        api_base_url="http://scheduler/dolphinscheduler",
        username="arena",
        password="secret",
        worker_group="default",
        tenant_code="default",
    )


def success(data):
    return FakeResponse({"code": 0, "success": True, "data": data})


def test_task_log_returns_cursor_and_message():
    session = FakeSession(
        [
            success(None),
            success({"lineNum": 12, "message": "line 11\nline 12\n"}),
        ]
    )
    client = DolphinSchedulerClient(settings(), session=session)

    result = client.task_log(
        task_instance_id=9,
        skip_line_num=10,
        limit=2,
    )

    assert result == {
        "line_num": 12,
        "message": "line 11\nline 12\n",
    }
    _, url, request = session.requests[-1]
    assert url.endswith("/log/detail")
    assert request["params"] == {
        "taskInstanceId": 9,
        "skipLineNum": 10,
        "limit": 2,
    }


def test_download_task_log_preserves_binary_response():
    session = FakeSession(
        [
            success(None),
            FakeResponse(
                content=b"complete log",
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Disposition": 'attachment; filename="task-9.log"',
                },
            ),
        ]
    )
    client = DolphinSchedulerClient(settings(), session=session)

    result = client.download_task_log(
        project_code=123,
        task_instance_id=9,
    )

    assert result.filename == "task-9.log"
    assert result.content == b"complete log"
    assert session.requests[-1][1].endswith(
        "/log/123/download-log"
    )


def test_execute_process_instance_uses_dolphinscheduler_execute_type():
    session = FakeSession([success(None), success(888)])
    client = DolphinSchedulerClient(settings(), session=session)

    result = client.execute_process_instance(
        project_code=123,
        process_instance_id=7,
        execute_type="STOP",
    )

    assert result == 888
    assert session.requests[-1][2]["params"] == {
        "processInstanceId": 7,
        "executeType": "STOP",
    }


def test_execute_task_instance_uses_scoped_task_endpoint():
    session = FakeSession([success(None), success(True)])
    client = DolphinSchedulerClient(settings(), session=session)

    result = client.execute_task_instance(
        project_code=123,
        task_instance_id=9,
        action="force-success",
    )

    assert result is True
    assert session.requests[-1][1].endswith(
        "/projects/123/task-instances/9/force-success"
    )
