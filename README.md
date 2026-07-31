# Arena Backend

FastAPI 服务和 DolphinScheduler 工作流定义。

## 启动

```powershell
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

接口文档：<http://127.0.0.1:8000/docs>

## 创建并发送增量更新任务

Python：

```python
from scheduler import create_and_submit_incremental_update

result = create_and_submit_incremental_update()
print(result)
```

也可以调用后端接口：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/scheduler/incremental-updates
```

后端通过官方 `apache-dolphinscheduler` SDK 连接 Python Gateway，不直接请求
DolphinScheduler HTTP API。工作流包含 14 个串行依赖的 Shell 任务，前一个
Runtime Worker 完成后才会启动下一个；这个限制只作用于该工作流，不限制
DolphinScheduler Worker 节点执行其它工作流。
提交时会创建一个仅执行一次的在线 schedule，由 DolphinScheduler 启动任务实例。

## 配置

默认读取项目根目录 `.env`。

| 变量 | 默认值 |
| --- | --- |
| `DOLPHINSCHEDULER_PYTHON_GATEWAY_ADDRESS` | `127.0.0.1` |
| `DOLPHINSCHEDULER_PYTHON_GATEWAY_PORT` | `25333` |
| `DOLPHINSCHEDULER_PYTHON_GATEWAY_AUTH_TOKEN` | 必填，必须与容器一致 |
| `DOLPHINSCHEDULER_USERNAME` | `arena-scheduler` |
| `DOLPHINSCHEDULER_PASSWORD` | `dolphinscheduler123` |
| `DOLPHINSCHEDULER_PROJECT_NAME` | `arena-runtime` |
| `DOLPHINSCHEDULER_WORKFLOW_NAME` | `incremental-update` |
| `DOLPHINSCHEDULER_WORKER_GROUP` | `default` |
| `DOLPHINSCHEDULER_TENANT_CODE` | `default` |
| `DOLPHINSCHEDULER_RUNTIME_COMMAND` | `/opt/arena-runtime/.venv/bin/core-manage` |
| `INCREMENTAL_UPDATE_THREADS` | `1` |
| `INCREMENTAL_UPDATE_THROTTLE` | `8` |
