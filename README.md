# Arena Backend

FastAPI 服务以及完整的 DolphinScheduler 工作流注册、任务提交、状态跟踪、控制和
日志访问封装。

## 代码结构

```text
app/
├── main.py                    # 应用入口和健康检查
└── routers/
    └── scheduler.py           # 调度 HTTP API
scheduler/
├── clients/
│   └── dolphinscheduler.py    # DolphinScheduler 3.2 HTTP 客户端
├── definitions/
│   ├── applications.py        # query/factor/backtest 工作流
│   ├── incremental.py         # 增量更新工作流
│   └── registry.py            # 工作流注册与发现
├── jobs/
│   ├── store.py               # 共享目录任务元数据
│   └── service.py             # 提交、同步、控制、日志服务
├── config.py
├── domain.py
└── errors.py
```

## 启动

```powershell
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

接口文档：<http://127.0.0.1:8000/docs>

FastAPI 初始化时会创建或更新 `query`、`factor`、`backtest` 和
`incremental-update` 四个工作流。DolphinScheduler 不可用或工作流注册失败时，
Backend 启动失败。

## 调度 API

### 工作流

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `GET` | `/api/v1/scheduler/workflows` | 查询当前注册的工作流 |
| `POST` | `/api/v1/scheduler/workflows` | 创建或更新全部工作流 |

如需在服务运行期间手动重新注册：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/scheduler/workflows
```

### 提交任务

提交 query 时，请求体就是 Runtime 的 query 输入，但不能包含 `output_dir`：

```powershell
$input = Get-Content ..\runtime\examples\query.json -Raw | ConvertFrom-Json
$input.PSObject.Properties.Remove("output_dir")
$job = Invoke-RestMethod `
  -Method Post `
  -ContentType application/json `
  -Body ($input | ConvertTo-Json -Depth 30) `
  -Uri http://127.0.0.1:8000/api/v1/scheduler/jobs/query
```

factor 使用 `/api/v1/scheduler/jobs/factor`，请求体对应 Runtime 的
`factor.json`，同样不能包含 `output_dir`。backtest 使用
`/api/v1/scheduler/jobs/backtest`。

增量更新同样创建可跟踪的 Arena Job，并立即启动已注册的工作流：

```powershell
$job = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/scheduler/incremental-updates
```

### 状态跟踪

查询任务详情时，响应包含工作流实例、全部 task instance、状态计数、开始/结束时间、
执行耗时、重试次数和 Parquet 产物：

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/scheduler/jobs/$($job.job_id)"
```

查询任务列表：

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/scheduler/jobs?application=factor&refresh=true"
```

`refresh=false` 只读取本地持久化状态；`refresh=true` 会逐个向 DolphinScheduler
同步最新状态。单任务详情始终同步最新状态。

backend 为每个任务创建独立目录：

```text
/shared/<query|factor|backtest|incremental-update>/<job-id>/
├── input.json
├── job.json
└── output/
    └── *.parquet
```

`input.json` 中的 `output_dir` 固定为相对路径 `output`。backend 通过
DolphinScheduler 的 `startParams` 将该文件的容器路径传给工作流，因此并发实例不会互相
覆盖参数或输出。

`incremental-update` 不需要 `input.json` 和 `output/`，但仍保存 `job.json`，
其中包含状态变化事件和历次工作流实例 ID。

### 实时日志

先从任务详情的 `tasks[].id` 获取 task instance ID，然后增量读取日志：

```powershell
$taskId = $status.tasks[0].id
$log = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/v1/scheduler/jobs/$($job.job_id)/tasks/$taskId/logs?skip_line_num=0&limit=1000"
```

下一次请求把 `skip_line_num` 设置为上次返回的 `line_num`，即可持续获取新增日志。
下载完整日志：

```powershell
Invoke-WebRequest `
  -OutFile task.log `
  -Uri "http://127.0.0.1:8000/api/v1/scheduler/jobs/$($job.job_id)/tasks/$taskId/logs/download"
```

### 控制任务

工作流实例支持以下操作：

```text
POST /api/v1/scheduler/jobs/<job-id>/actions/stop
POST /api/v1/scheduler/jobs/<job-id>/actions/pause
POST /api/v1/scheduler/jobs/<job-id>/actions/resume
POST /api/v1/scheduler/jobs/<job-id>/actions/rerun
POST /api/v1/scheduler/jobs/<job-id>/actions/retry-failed
```

task instance 支持：

```text
POST /api/v1/scheduler/jobs/<job-id>/tasks/<task-id>/actions/stop
POST /api/v1/scheduler/jobs/<job-id>/tasks/<task-id>/actions/force-success
```

`rerun` 和 `retry-failed` 会把原工作流实例 ID 放入
`process_instance_history`，随后自动跟踪新实例。

### 审计日志

```text
GET /api/v1/scheduler/audit-logs
GET /api/v1/scheduler/audit-logs/types
```

审计日志支持 `model_types`、`operation_types`、时间范围、用户名和对象名称过滤。

## 增量更新工作流

工作流包含 14 个相互独立的 Runtime 增量更新根任务，它们在 DAG 中采用并行结构。
Backend 自动创建 `tushare-api` Task Group，并将容量保持为 `1`，因此所有工作流
实例中实际同时只会运行一个 Tushare 更新任务，其余任务在 Task Group 中等待。

任务使用 `task_group_priority` 保持原有更新顺序。单个任务失败并完成重试后会释放
Task Group 槽位，不会阻止其他根任务继续执行。全部 14 个任务结束后由一个
Condition 汇总状态，只要存在失败任务，工作流最终状态就是失败。

```python
from scheduler import create_and_submit_incremental_update

result = create_and_submit_incremental_update()
print(result)
```

## PostgreSQL

Compose 只使用一个 PostgreSQL 数据库：

- DolphinScheduler 的 `t_ds_*` 表由官方 schema initializer 管理。
- Backend 的表位于 `arena_backend` schema，由 Alembic revision 管理。

Backend 容器启动时会先执行 `alembic upgrade head`，迁移成功后再启动 API。
`GET /health` 只有在数据库可连接且当前 schema 为 `arena_backend` 时才返回 HTTP 200。

创建新迁移：

```powershell
cd backend
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

## 配置

默认读取项目根目录 `.env`。

| 变量 | 默认值 |
| --- | --- |
| `ARENA_SHARED_DIR` | `/shared` |
| `DATABASE_URL` | 必填，backend PostgreSQL 连接地址 |
| `DOLPHINSCHEDULER_BASE_URL` | `http://127.0.0.1:12345/dolphinscheduler` |
| `DOLPHINSCHEDULER_PYTHON_GATEWAY_ADDRESS` | `127.0.0.1` |
| `DOLPHINSCHEDULER_PYTHON_GATEWAY_PORT` | `25333` |
| `DOLPHINSCHEDULER_PYTHON_GATEWAY_AUTH_TOKEN` | 必填，必须与容器一致 |
| `DOLPHINSCHEDULER_USERNAME` | `arena-scheduler` |
| `DOLPHINSCHEDULER_PASSWORD` | `dolphinscheduler123` |
| `DOLPHINSCHEDULER_PROJECT_NAME` | `arena-runtime` |
| `DOLPHINSCHEDULER_WORKFLOW_NAME` | `incremental-update` |
| `DOLPHINSCHEDULER_QUERY_WORKFLOW_NAME` | `query` |
| `DOLPHINSCHEDULER_FACTOR_WORKFLOW_NAME` | `factor` |
| `DOLPHINSCHEDULER_BACKTEST_WORKFLOW_NAME` | `backtest` |
| `DOLPHINSCHEDULER_INCREMENTAL_TASK_GROUP_NAME` | `tushare-api` |
| `DOLPHINSCHEDULER_INCREMENTAL_TASK_GROUP_SIZE` | `1` |
| `DOLPHINSCHEDULER_WORKER_GROUP` | `default` |
| `DOLPHINSCHEDULER_TENANT_CODE` | `default` |
| `DOLPHINSCHEDULER_RUNTIME_COMMAND` | `/opt/arena-runtime/.venv/bin/core-manage` |
