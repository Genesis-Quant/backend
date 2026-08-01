from main import app


def route_methods(path: str) -> set[str]:
    return {method.upper() for method in app.openapi()["paths"].get(path, {})}


def test_business_apps_only_submit_and_serve_completed_results():
    for application in ("query", "factor", "backtest"):
        prefix = f"/api/v1/{application}/tasks"
        assert route_methods(prefix) == {"POST"}
        assert route_methods(f"{prefix}/{{task_id}}/outputs") == {"GET"}
        assert route_methods(f"{prefix}/{{task_id}}/outputs/{{name}}") == {"GET"}


def test_runtime_operations_are_exposed_by_tasks_app():
    assert route_methods("/api/v1/tasks/{task_id}") == {"GET", "DELETE"}
    assert route_methods("/api/v1/tasks/{task_id}/logs") == {"GET"}
    assert route_methods("/api/v1/tasks/{task_id}/logs/download") == {"GET"}
    assert route_methods("/api/v1/tasks/{task_id}/actions/{action}") == {"POST"}
