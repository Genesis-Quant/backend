# Arena Backend

FastAPI 服务和 DolphinScheduler 工作流定义。

## 启动

```powershell
cd D:\Arena\backend
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

HTTP：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/scheduler/incremental-updates
```

函数会幂等创建 DolphinScheduler Project、一个容量为 1 的全局增量 Task Group，
以及包含全部独立 Shell 节点的工作流，然后发布并立即启动工作流。
`docker-compose.yml` 将每个 DolphinScheduler Worker 容器的执行线程数设为 1。
14 个 Runtime Worker 会分别生成任务实例和日志，但通过共享 Task Group 依次运行，
保证当前 Tushare 限速器不会因多进程而叠加请求速率。

## 配置

默认读取 `D:\Arena\.env`。生产环境由进程或容器同时注入 `.env` 和
`.env.prod`，后者覆盖前者。

| 变量 | 默认值 |
| --- | --- |
| `DOLPHINSCHEDULER_BASE_URL` | `http://127.0.0.1:12345/dolphinscheduler` |
| `DOLPHINSCHEDULER_USERNAME` | `admin` |
| `DOLPHINSCHEDULER_PASSWORD` | `dolphinscheduler123` |
| `DOLPHINSCHEDULER_PROJECT_NAME` | `arena-runtime` |
| `DOLPHINSCHEDULER_WORKFLOW_NAME` | `incremental-update` |
| `DOLPHINSCHEDULER_TASK_GROUP_PREFIX` | `arena-incremental` |
| `DOLPHINSCHEDULER_WORKER_GROUP` | `default` |
| `DOLPHINSCHEDULER_TENANT_CODE` | `default` |
| `DOLPHINSCHEDULER_RUNTIME_COMMAND` | `/opt/arena-runtime/.venv/bin/core-manage` |
| `INCREMENTAL_UPDATE_THREADS` | `1` |
| `INCREMENTAL_UPDATE_THROTTLE` | `8` |
