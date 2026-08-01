from types import SimpleNamespace

import requests

from config.dolphinscheduler.client import DolphinSchedulerClient


def test_task_log_returns_absolute_next_cursor():
    client = DolphinSchedulerClient(SimpleNamespace(), session=requests.Session())
    client.request = lambda method, path, params: {"lineNum": 25, "message": "line\n"}

    page = client.task_log(task_instance_id=10, skip_line_num=50, limit=25)

    assert page == {
        "skip_line_num": 50,
        "returned_lines": 25,
        "next_line_num": 75,
        "has_more": True,
        "message": "line\n",
    }
    client.session.close()

