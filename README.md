# Arena Backend

Backend 提供用户认证，以及 query、factor、backtest 三类 DolphinScheduler 后台任务。
DolphinScheduler 是 `config/dolphinscheduler` 中的运行基础设施，不是业务 app，也不暴露
`/api/v1/scheduler` 接口。

## 结构

```text
main.py
config/
├── database.py
├── settings.py
└── dolphinscheduler/
    ├── client.py              # DolphinScheduler HTTP 客户端
    ├── workflows.py           # 工作流注册和查询
    ├── applications.py        # query/factor/backtest 工作流定义
    ├── incremental.py         # 数据增量更新工作流定义
    └── task_groups.py
apps/
├── users/
├── query/
│   ├── models.py
│   ├── schemas.py
│   ├── services.py            # 提交 query 并读取完成结果
│   ├── views.py
│   └── tests/
├── factor/                    # 与 query 相同的 app 结构
├── backtest/                  # 与 query 相同的 app 结构
├── tasks/                     # 提交执行、状态同步、日志、控制和轮询
└── utils/                     # 三个业务 app 共用的结果读取和参数校验
```

三个业务 app 分别拥有 `query_tasks`、`factor_tasks`、`backtest_tasks` 表。每条记录归属
一个用户，并保存：

- `task_id`：实际 DolphinScheduler task instance ID，不是启动流程返回的 command ID。
- `process_instance_id`、`process_definition_code` 和 `project_code`：用于同步运行状态。
- `payload` 和 `requested_outputs`：原始运行参数及选定结果。
- `input_file` 和 `output_dir`：该任务独占的共享目录。
- `state`、`process_state`、worker、重试次数、运行时间和失败日志摘要。
- task/process ID 历史、状态历史、控制事件和最后同步时间。

DolphinScheduler 异步创建 task instance。提交接口等待最多 10 秒，只在获得真实 `task_id`
后才返回；提交后的状态、日志和控制全部通过通用 tasks API 完成。Backend 每隔 5 秒轮询
非终态任务，PostgreSQL advisory lock 保证部署多个 Backend 进程时同一轮只有一个轮询器工作。

## API

所有任务接口都需要登录获得的 Bearer JWT，且只能访问当前用户自己的记录。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `POST` | `/api/v1/query/tasks` | 提交 query 后台任务 |
| `GET` | `/api/v1/query/tasks/{task_id}/outputs` | 任务成功后列出 query 结果 |
| `GET` | `/api/v1/query/tasks/{task_id}/outputs/{name}` | 下载指定 query 结果 |

factor 和 backtest 使用完全相同的路径结构，只需将路径中的 `query` 换成 `factor` 或
`backtest`。业务 app 不提供任务列表、状态详情、日志或控制接口。

结果接口只查询 Backend 已同步的数据库状态，不会调用 DolphinScheduler。任务不是
`SUCCESS` 时返回 HTTP 409；任务成功但缺少约定结果时返回 HTTP 502；未授权、不存在或
没有请求指定结果时返回 HTTP 404。

### 通用 task_id 网关

以下接口的 `{task_id}` 都是实际 DolphinScheduler task instance ID。Backend 会先在
query、factor、backtest 三张表中校验该 ID 是否归属当前用户，未授权和不存在统一返回
HTTP 404。

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `GET` | `/api/v1/tasks/{task_id}` | 同步并返回 task、process、worker、重试、耗时和状态历史 |
| `GET` | `/api/v1/tasks/{task_id}/logs` | 分页读取日志 |
| `GET` | `/api/v1/tasks/{task_id}/logs/download` | 流式下载完整日志 |
| `POST` | `/api/v1/tasks/{task_id}/actions/{action}` | 控制 task 或 process instance |
| `DELETE` | `/api/v1/tasks/{task_id}` | 删除终态任务的数据库记录和 shared 目录 |

支持的 action 为 `stop`、`force-success`、`pause`、`resume`、`rerun` 和
`retry-failed`。重跑后旧 task ID 会写入历史，仍可用于读取旧日志和鉴权，新 task instance
出现后数据库会保存新的 `task_id`。

提交参数就是对应 Runtime API 参数，再增加必填 `output`。调用方不能传 `output_dir`，
Backend 会创建以下目录并写入 Runtime 输入：

```text
/shared/<query|factor|backtest>/<database-record-id>/
├── input.json
└── output/
    └── *.parquet
```

例如提交 query：

```powershell
$auth = Invoke-RestMethod -Method Post -ContentType application/json `
  -Body '{"username":"arena_user","password":"secure-password"}' `
  -Uri http://127.0.0.1:8000/api/v1/auth/login

$body = @{
  dataset_query = @{
    start_date = "2025-01-01"
    end_date = "2025-01-31"
    codes = @("000001.SZ")
    factors = @("close")
  }
  output = @("data")
} | ConvertTo-Json -Depth 20

$task = Invoke-RestMethod -Method Post -ContentType application/json `
  -Headers @{ Authorization = "Bearer $($auth.access_token)" } `
  -Body $body -Uri http://127.0.0.1:8000/api/v1/query/tasks
```

提交响应只包含可直接用于通用网关的真实 `task_id`：

```json
{"task_id": 123}
```

日志分页响应包含当前偏移、本页行数和下一页绝对游标：

```json
{
  "task_id": 123,
  "state": "RUNNING_EXECUTION",
  "skip_line_num": 50,
  "returned_lines": 25,
  "next_line_num": 75,
  "has_more": true,
  "message": "..."
}
```

下一次将 `next_line_num` 作为 `skip_line_num`。完整日志由 Backend 流式转发，不会把整个
日志文件加载进内存。

## 启动与迁移

```powershell
cd backend
uv sync
uv run alembic upgrade head
uv run pytest
uv run uvicorn main:app --reload
```

Compose 使用外部 PostgreSQL。DolphinScheduler 表由官方初始化器管理，Backend 表位于
`arena_backend` schema，由 Alembic 管理。`GET /health` 会检查数据库和 schema。

Backend 启动时创建或更新 query、factor、backtest 和现有增量更新工作流。增量更新属于
基础设施工作流，不再有 scheduler app 或通用 job API。

状态轮询配置：

| 变量 | 默认值 |
| --- | --- |
| `DOLPHINSCHEDULER_POLL_INTERVAL_SECONDS` | `5` |
| `DOLPHINSCHEDULER_POLL_BATCH_SIZE` | `100` |
