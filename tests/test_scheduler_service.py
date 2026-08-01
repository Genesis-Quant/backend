from types import SimpleNamespace

import scheduler.jobs.service as service_module
from scheduler.domain import JobAction, TaskAction
from scheduler.jobs import SchedulerService, SharedJobStore


class FakeClient:
    def __init__(self):
        self.started = []
        self.actions = []
        self.task_actions = []
        self.log_requests = []
        self.job_id = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def start_process_instance(self, **request):
        self.started.append(request)
        self.job_id = request["start_params"]["job_id"]
        return 901

    def process_instances(self, **_):
        return [
            {
                "id": 71,
                "name": "factor-run",
                "state": "RUNNING_EXECUTION",
                "globalParams": f'[{{"value":"{self.job_id}"}}]',
                "startTime": "2026-07-31 15:00:00",
                "endTime": None,
            }
        ]

    def process_instance(self, _project_code, process_instance_id):
        return {
            "id": process_instance_id,
            "name": "factor-run",
            "state": "RUNNING_EXECUTION",
            "startTime": "2026-07-31 15:00:00",
            "endTime": None,
        }

    def process_instance_tasks(self, **_):
        return [
            {
                "id": 81,
                "name": "factor",
                "taskType": "SHELL",
                "state": "RUNNING_EXECUTION",
                "submitTime": "2026-07-31 15:00:00",
                "startTime": "2026-07-31 15:00:01",
                "endTime": None,
                "retryTimes": 0,
                "maxRetryTimes": 0,
                "taskComplete": False,
            }
        ]

    def task_log(self, **request):
        self.log_requests.append(request)
        return {"line_num": 3, "message": "running\n"}

    def execute_process_instance(self, **request):
        self.actions.append(request)
        return 902

    def execute_task_instance(self, **request):
        self.task_actions.append(request)
        return 903


def make_service(tmp_path, monkeypatch):
    current_settings = SimpleNamespace(shared_dir=tmp_path)
    client = FakeClient()
    monkeypatch.setattr(
        service_module,
        "ensure_workflow_definition",
        lambda workflow, settings: (
            11,
            {"code": 22, "name": workflow},
        ),
    )
    service = SchedulerService(
        current_settings,
        store=SharedJobStore(tmp_path),
        client_factory=lambda: client,
    )
    return service, client


def factor_payload():
    return {
        "dataset_query": {"start_date": "2025-01-01"},
        "factor_columns": ["close"],
        "return_columns": ["pct_chg"],
        "output": ["processed_data", "information_coefficient"],
    }


def test_service_tracks_process_tasks_and_log_cursor(tmp_path, monkeypatch):
    service, client = make_service(tmp_path, monkeypatch)
    submitted = service.submit_application("factor", factor_payload())

    assert client.started[0]["start_params"]["output"] == "processed_data information_coefficient"

    tracked = service.get_job(submitted["job_id"])

    assert tracked["state"] == "RUNNING_EXECUTION"
    assert tracked["process_instance"]["id"] == 71
    assert tracked["task_summary"]["states"] == {
        "RUNNING_EXECUTION": 1
    }
    assert tracked["tasks"][0]["id"] == 81

    log = service.get_task_log(
        submitted["job_id"],
        81,
        skip_line_num=1,
        limit=2,
    )
    assert log["line_num"] == 3
    assert log["message"] == "running\n"
    assert client.log_requests == [
        {
            "task_instance_id": 81,
            "skip_line_num": 1,
            "limit": 2,
        }
    ]


def test_service_controls_running_job(tmp_path, monkeypatch):
    service, client = make_service(tmp_path, monkeypatch)
    submitted = service.submit_application("factor", factor_payload())
    service.get_job(submitted["job_id"], include_tasks=False)

    controlled = service.control_job(
        submitted["job_id"],
        JobAction.STOP,
    )

    assert controlled["last_action"] == "stop"
    assert client.actions == [
        {
            "project_code": 11,
            "process_instance_id": 71,
            "execute_type": "STOP",
        }
    ]


def test_incremental_update_is_submitted_as_tracked_job(tmp_path, monkeypatch):
    service, client = make_service(tmp_path, monkeypatch)

    submitted = service.submit_incremental_update()

    assert submitted["application"] == "incremental-update"
    assert submitted["state"] == "SUBMITTED"
    assert submitted["input_file"] is None
    assert client.started[0]["start_params"] == {
        "job_id": submitted["job_id"]
    }


def test_service_controls_owned_task(tmp_path, monkeypatch):
    service, client = make_service(tmp_path, monkeypatch)
    submitted = service.submit_application("factor", factor_payload())

    result = service.control_task(
        submitted["job_id"],
        81,
        TaskAction.STOP,
    )

    assert result["scheduler_submission"] == 903
    assert client.task_actions == [
        {
            "project_code": 11,
            "task_instance_id": 81,
            "action": "stop",
        }
    ]
