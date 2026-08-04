# Arena Backend

Backend 提供用户认证，以及 Query、Factor、Backtest 和 Incremental Update 的
DolphinScheduler 工作流提交、状态跟踪、结果访问与 Task 日志能力。

DolphinScheduler 接入位于 `core/scheduler`；业务 API 不直接暴露调度器的原始接口。

## 结构

```text
main.py
config.py
core/
├── apps/
│   ├── users/
│   ├── query/
│   ├── factor/
│   ├── backtest/
│   ├── incremental/
│   ├── workflows/            # 工作流提交、状态、控制和轮询
│   └── tasks/                # DolphinScheduler Task 日志和 Task 操作
├── database/
├── scheduler/                 # DolphinScheduler 客户端与工作流定义
└── utils/
```

## 数据模型

Backend 只持久化应用提交和 DolphinScheduler workflow instance，不保存 Task 快照：

- `workflow_runs`：提交参数、请求输出、共享目录、工作流定义编码和提交状态。
- `workflow_instances`：以 `workflow_instance_id` 为主键，保存工作流状态、时间、错误和状态历史。
- `<application>_workflow_runs`：Query、Factor、Backtest、Incremental 的应用扩展信息。
- `factor_versions`、`backtest_versions`：直接关联保存结果对应的 `workflow_instance_id`。

一个工作流可以包含多个 DolphinScheduler Task。工作流详情和列表展示的 Task 定义、Task
instance 状态、Worker、重试次数与耗时均在请求时实时从 DolphinScheduler 查询。Task ID
专指 DolphinScheduler `task instance id`。

Backend 每隔 5 秒轮询待创建或非终态的工作流。PostgreSQL advisory lock 保证部署多个
Backend 进程时，同一轮只有一个轮询器执行。

## API

所有业务接口都需要登录获得的 Bearer JWT。普通用户只能访问自己的工作流；管理员可以
查看全部工作流。

### 提交与结果

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `POST` | `/api/v1/query/workflows` | 提交一次 Query 工作流 |
| `GET` | `/api/v1/query/workflows/{workflow_instance_id}/outputs` | 列出 Query 结果 |
| `GET` | `/api/v1/query/workflows/{workflow_instance_id}/outputs/{name}` | 下载 Query Parquet 结果 |

Factor 和 Backtest 的一次性工作流使用相同结构，将路径中的 `query` 替换为 `factor` 或
`backtest`。提交参数为对应 Runtime 参数并增加必填 `output`；调用方不能指定
`output_dir`。

工作流输入 JSON 始终写入 `ARENA_SHARED_DIR`，供 DolphinScheduler Worker 读取。默认情况下 Parquet
结果也写入该共享目录；设置 `ARENA_SHARED_CLOUD=True` 后，Backend 会为 Runtime 追加
`--output-cloud`，结果写入 `OBJECT_STORAGE_ROOT_FOLDER/<application>/<workspace>/output`。
结果列表和下载接口根据工作流记录中的本地路径或 `s3://` URI 自动选择本地文件或对象存储，
因此切换配置不会破坏已有任务的结果读取。

提交响应同时包含内部提交记录 ID 和作为后续查询主键的 workflow instance ID：

```json
{
  "record_id": 42,
  "workflow_instance_id": 123
}
```

结果接口只读取 Backend 已同步的状态和共享目录，不会调用 DolphinScheduler。工作流不是
`SUCCESS` 时返回 HTTP 409；成功但缺少约定结果时返回 HTTP 502；未授权或不存在时返回
HTTP 404。

### 工作流实例

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `GET` | `/api/v1/workflows` | 分页查询工作流，并实时附带各 Task 状态 |
| `GET` | `/api/v1/workflows/{workflow_instance_id}` | 同步并读取指定工作流及其 Tasks |
| `POST` | `/api/v1/workflows/{workflow_instance_id}/actions/{action}` | 控制工作流 |
| `DELETE` | `/api/v1/workflows/{workflow_instance_id}` | 删除终态工作流记录 |

工作流 action 包括 `stop`、`pause`、`resume`、`rerun` 和 `retry-failed`。重新运行只允许
当前且未保存为研究版本的工作流。

### DolphinScheduler Task

Task API 必须同时传入 `workflow_instance_id`，Backend 会实时确认 Task 确实属于该工作流：

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `GET` | `/api/v1/tasks/{task_instance_id}/logs?workflow_instance_id=...` | 分页读取 Task 日志 |
| `GET` | `/api/v1/tasks/{task_instance_id}/logs/download?workflow_instance_id=...` | 流式下载完整日志 |
| `POST` | `/api/v1/tasks/{task_instance_id}/actions/force-success?workflow_instance_id=...` | 将 Task 强制标记成功 |

日志分页响应包含下一页绝对游标：

```json
{
  "workflow_instance_id": 123,
  "task_instance_id": 456,
  "state": "RUNNING_EXECUTION",
  "skip_line_num": 50,
  "returned_lines": 25,
  "next_line_num": 75,
  "has_more": true,
  "message": "..."
}
```

下一次请求将 `next_line_num` 作为 `skip_line_num`。完整日志由 Backend 流式转发，不会
一次性加载到内存。

### 研究项目与版本

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| `GET/POST` | `/api/v1/<query|factor|backtest>/projects` | 查询或创建项目 |
| `GET/DELETE` | `/api/v1/query/projects/{project_id}` | 读取或删除 Query 项目 |
| `GET/PATCH/DELETE` | `/api/v1/<factor|backtest>/projects/{project_id}` | 读取、改名或删除研究项目 |
| `POST` | `/api/v1/query/projects/{project_id}/queries` | 提交项目 Query 工作流 |
| `POST` | `/api/v1/factor/projects/{project_id}/analyses` | 提交因子分析工作流 |
| `POST` | `/api/v1/backtest/projects/{project_id}/runs` | 提交策略回测工作流 |
| `GET/POST` | `/api/v1/<factor|backtest>/projects/{project_id}/versions` | 查询或保存版本 |
| `GET` | `/api/v1/<factor|backtest>/projects/{project_id}/versions/{version}` | 读取指定版本 |

保存的 Factor/Backtest 版本直接绑定产生结果的 workflow instance，后续读取结果不会因同一
项目再次运行而改变。

## 共享目录

```text
/shared/<query|factor|backtest>/<workspace-key>/
├── input.json
└── output/
    └── *.parquet
```

## 启动与迁移

```powershell
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn main:app --reload
```

Backend 表由 Alembic 管理。`GET /health` 会检查当前 PostgreSQL 数据库和 schema。Backend
启动时会确保 Query、Factor、Backtest 和 Incremental Update 工作流定义存在。

状态轮询配置：

| 变量 | 默认值 |
| --- | --- |
| `DOLPHINSCHEDULER_POLL_INTERVAL_SECONDS` | `5` |
| `DOLPHINSCHEDULER_POLL_BATCH_SIZE` | `100` |
