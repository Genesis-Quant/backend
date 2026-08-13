# Arena MCP

Arena MCP 提供 Query、Factor 和 Backtest 三类项目的创建、提交、状态查询、日志、结果与版本操作。
本页只定义连接方式和通用调用顺序。请求字段见对应业务文档，精确 JSON 结构见对应 Schema。

## 连接

- Endpoint：`{ARENA_PUBLIC_URL}/mcp`
- Transport：MCP Streamable HTTP
- 服务为无状态模式，每个 HTTP 请求都必须携带 `Authorization: Bearer <access_token>`
- MCP 不提供登录工具；Token 来自 Arena REST 登录接口

```http
POST {ARENA_PUBLIC_URL}/api/v1/auth/login
Content-Type: application/json

{"username":"your_username","password":"your_password"}
```

使用响应中的 `access_token`。HTTP 401 `invalid_token` 表示 Bearer Token 缺失、无效或已过期。

## 工具返回值

所有工具使用统一 envelope。业务结果位于：

```text
CallToolResult.structuredContent.result
```

例如项目 ID 是 `create_project` 返回值的 `structuredContent.result.id`。不要解析
`content[].text`，也不要把 `structuredContent` 本身当作业务结果。

工具校验或执行失败时 MCP 返回 `isError=true`。成功提交一个异步工作流不代表工作流已经成功，
必须继续轮询 Workspace。

## 构造请求前读取什么

| 操作 | 需要读取 |
| --- | --- |
| 任意操作 | `arena://docs/tools` |
| Query | `arena://docs/query`、`arena://docs/dsl`、`arena://schemas/query` |
| Factor | `arena://docs/factor`、`arena://docs/dsl`、`arena://schemas/factor` |
| Backtest | `arena://docs/backtest`、`arena://docs/dolphindb-backtest`、`arena://docs/dsl`、`arena://schemas/backtest` |

`arena://schemas/*` 定义顶层字段。DSL 节点是动态分发模型，构造某个节点前使用
`describe_dsl_operator` 读取该算符的 `definition`，不要根据同名 Python、Pandas、TA-Lib 或
DolphinDB 函数猜 `fields` 和 `params`。

三个 `run_*` 工具的 Tool Schema 将业务对象保留为通用 JSON object，服务端仍会用 Runtime
模型严格校验。完整顶层结构以 `arena://schemas/*` 为准，具体算符结构以
`describe_dsl_operator` 为准。

## 通用调用顺序

```text
create_project(application, title)
  -> project_id = result.id

run_query(project_id, request)
run_factor_analysis(project_id, parameters)
run_backtest(project_id, parameters)
  -> workspace_id, workflow_instance_id

get_workspace_status(workspace_id)
  -> 轮询到 SUCCESS 或失败终态

list_workflow_outputs(application, workflow_instance_id)
  -> 输出元数据与 download_path
```

`workspace_id` 标识业务工作空间。一次 Workspace 可以产生多个 Attempt，重跑后
`workflow_instance_id` 会变化。因此执行过程中按 `workspace_id` 轮询，并使用响应中当前的
`workflow_instance_id` 读取详情、日志和输出。

## 状态处理

| 状态 | 含义 | 调用方行为 |
| --- | --- | --- |
| `QUEUED`、`CREATED`、`SUBMITTING`、`SUBMITTED` | 创建或提交中 | 继续轮询 Workspace |
| DolphinScheduler 非终态，如 `RUNNING_EXECUTION` | 调度任务运行中 | 继续轮询 |
| `SUCCESS` | 工作流完成 | 读取输出；Factor/Backtest 可保存版本 |
| `AUTO_SAVE_PENDING`、`RESULT_PENDING` | 调度已结束，后端处理结果中 | 继续轮询，不要停止已结束实例 |
| `SUBMIT_FAILED`、`FAILURE`、`AUTO_SAVE_FAILED` | 提交、执行或后处理失败 | 读取 error、详情和完整日志 |
| `STOP`、`KILL` | 已终止 | 不读取输出 |

失败诊断：

1. 读取 `get_workspace_status` 的 `error` 和 `events`；
2. 若已有 `workflow_instance_id`，调用 `get_workflow_details`；
3. 从 `tasks` 找到失败的 `task_instance_id`；
4. 调用 `get_task_logs`，从 `skip_line_num=0` 开始；
5. 当 `has_more=true` 时，将返回的 `next_line_num` 作为下一页游标。

## 项目与版本

- Query 项目没有版本，再次运行会更新当前请求和结果；每个用户最多创建 5 个 Query 项目。
- Factor 和 Backtest 项目创建时包含一个可更新的未保存版本。
- 成功运行后可调用 `save_version` 固化当前版本；后端随后创建下一个未保存版本。
- `list_versions`、`get_version`、`save_version` 只适用于 Factor 和 Backtest。

## 输出下载

`list_workflow_outputs` 不返回 Parquet 内容，只返回逻辑名称、文件名、大小、修改时间和
`download_path`。将 `download_path` 拼接到当前 MCP Endpoint 的 origin，并使用相同 Bearer Token
下载。不要把一个部署环境签发的 Token 发送到另一个域名。

## 权限与副作用

项目、Workspace、Instance、Task 日志和结果都按当前 Token 对应用户鉴权。普通用户只能访问自己
的资源。以下工具会写入状态：`create_project`、三个 `run_*`、`save_version`、
`control_workflow`；其它工具只读。
