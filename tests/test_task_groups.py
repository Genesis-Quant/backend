from types import SimpleNamespace

import scheduler.resources.task_groups as task_groups


class FakeClient:
    def __init__(self, group=None):
        self.group = group
        self.created = []
        self.updated = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def project_code(self, _project_name):
        return 123

    def task_groups(self, **_):
        return [self.group] if self.group is not None else []

    def create_task_group(self, **request):
        self.created.append(request)
        self.group = {
            "id": 9,
            "name": request["name"],
            "description": request["description"],
            "groupSize": request["group_size"],
        }

    def update_task_group(self, **request):
        self.updated.append(request)
        self.group = {
            "id": request["task_group_id"],
            "name": request["name"],
            "description": request["description"],
            "groupSize": request["group_size"],
        }


def settings():
    return SimpleNamespace(
        project_name="arena-runtime",
        incremental_task_group_name="tushare-api",
        incremental_task_group_size=1,
    )


def test_ensure_incremental_task_group_creates_capacity_one(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(
        task_groups,
        "DolphinSchedulerClient",
        lambda _settings: client,
    )

    result = task_groups.ensure_incremental_task_group(settings())

    assert result["id"] == 9
    assert result["groupSize"] == 1
    assert client.created == [
        {
            "project_code": 123,
            "name": "tushare-api",
            "description": (
                task_groups.INCREMENTAL_TASK_GROUP_DESCRIPTION
            ),
            "group_size": 1,
        }
    ]


def test_ensure_incremental_task_group_repairs_capacity(monkeypatch):
    client = FakeClient(
        {
            "id": 9,
            "name": "tushare-api",
            "description": "old",
            "groupSize": 5,
        }
    )
    monkeypatch.setattr(
        task_groups,
        "DolphinSchedulerClient",
        lambda _settings: client,
    )

    result = task_groups.ensure_incremental_task_group(settings())

    assert result["groupSize"] == 1
    assert client.updated[0]["task_group_id"] == 9
    assert client.updated[0]["group_size"] == 1
