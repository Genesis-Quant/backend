import asyncio
from types import SimpleNamespace

from app import main
from scheduler.definitions import registry as workflows


def test_ensure_all_workflows_includes_incremental(monkeypatch):
    settings = SimpleNamespace(
        project_name="arena-runtime",
        workflow_name="incremental-update",
    )
    monkeypatch.setattr(
        workflows,
        "create_application_workflows",
        lambda current_settings: {
            "project_name": current_settings.project_name,
            "workflows": {
                "query": {"name": "query", "code": 1},
                "factor": {"name": "factor", "code": 2},
                "backtest": {"name": "backtest", "code": 3},
            },
        },
    )
    monkeypatch.setattr(
        workflows,
        "create_incremental_update_workflow",
        lambda current_settings: {
            "name": current_settings.workflow_name,
            "workflow_code": 4,
            "worker_task_count": 14,
            "control_task_count": 3,
            "task_count": 17,
            "task_group": {
                "id": 5,
                "name": "tushare-api",
                "group_size": 1,
            },
        },
    )

    result = workflows.ensure_all_workflows(settings)

    assert result == {
        "project_name": "arena-runtime",
        "workflows": {
            "query": {"name": "query", "code": 1},
            "factor": {"name": "factor", "code": 2},
            "backtest": {"name": "backtest", "code": 3},
            "incremental-update": {
                "name": "incremental-update",
                "code": 4,
                "worker_task_count": 14,
                "control_task_count": 3,
                "task_count": 17,
                "task_group": {
                    "id": 5,
                    "name": "tushare-api",
                    "group_size": 1,
                },
            },
        },
    }


def test_fastapi_lifespan_ensures_all_workflows(monkeypatch):
    expected = {
        "project_name": "arena-runtime",
        "workflows": {},
    }
    monkeypatch.setattr(main, "ensure_all_workflows", lambda: expected)

    async def enter_lifespan() -> None:
        async with main.lifespan(main.app):
            assert main.app.state.workflows == expected

    asyncio.run(enter_lifespan())
