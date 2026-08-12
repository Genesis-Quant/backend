# Arena MCP 调用协议

本文只描述 Arena 的业务调用顺序和返回契约。请求字段分别由
`arena://schemas/query`、`arena://schemas/factor`、`arena://schemas/backtest` 定义；
不要从示例反推 Schema。

## 1. 连接

- Endpoint：`{ARENA_PUBLIC_URL}/mcp`
- Transport：MCP Streamable HTTP
- Header：`Authorization: Bearer <Arena access_token>`
- Token 来源：`POST /api/v1/auth/login`
- 每个请求都必须携带 Bearer Token；Arena MCP 是无状态 HTTP 服务

所有工具返回统一放在：

```text
CallToolResult.structuredContent.result
```

不要解析 `content[].text`，也不要跳过 `result` 包装层。

## 2. 先读什么

| 目标 | 必读 Resource | 构造时使用的发现工具 |
| --- | --- | --- |
| Query | `arena://docs/query`、`arena://docs/dsl`、`arena://schemas/query` | `list_dsl_operators`、`describe_dsl_operator` |
| Factor | `arena://docs/factor`、`arena://docs/dsl`、`arena://schemas/factor` | 同上 |
| Backtest | `arena://docs/backtest`、`arena://docs/dolphindb-backtest`、`arena://schemas/backtest` | DSL 工具；不确定 DolphinDB 内置函数签名时调用 `describe_dolphindb_functions` |

文档说明 Arena 业务语义；Schema 决定字段类型和必填项；发现工具返回当前 Runtime 或
DolphinDB 实际支持的算符/函数。三者冲突时，不要猜，停止调用并报告冲突。

## 3. 标准调用链

```text
create_project / list_projects
        ↓ project_id
run_query / run_factor_analysis / run_backtest
        ↓ workspace_id + workflow_instance_id
get_workspace_status(workspace_id)  每 3–5 秒轮询
        ↓ SUCCESS
list_workflow_outputs(application, workflow_instance_id)
        ↓
Factor/Backtest 可选 save_version
```

`workspace_id` 是业务工作空间；同一个 Workspace 以后可以产生多个 Attempt。
`workflow_instance_id` 是 DolphinScheduler 的一次实际执行。执行期间始终轮询最初返回的
`workspace_id`，不要假设重试后 instance ID 不变。

## 4. 项目工具

### create_project

输入：

```json
{"application":"backtest","title":"波动率目标策略"}
```

读取 `structuredContent.result.id` 作为 `project_id`。Query 项目每位用户最多 5 个；
Factor 和 Backtest 项目没有这一限制。

### get_project

返回项目、最新保存版本和当前草稿。回读一次运行实际保存的请求时，使用：

```text
result.draft.parameters
```

不要把 `latest_version` 的参数当作当前草稿参数。

## 5. 工作流状态

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| `QUEUED`、`SUBMITTING`、`SUBMITTED` | 正在提交 | 继续轮询 Workspace |
| DolphinScheduler 非终态 | 正在执行 | 继续轮询 |
| `SUCCESS` | 结果已生成 | `list_workflow_outputs` |
| `AUTO_SAVE_PENDING`、`RESULT_PENDING` | 调度完成，后端处理结果 | 继续轮询，不要停止实例 |
| `FAILURE`、`SUBMIT_FAILED`、`AUTO_SAVE_FAILED` | 失败 | 读取 `error`；需要完整日志时调用详情和日志工具 |
| `STOP`、`KILL` | 用户或调度器终止 | 不读取输出 |

失败诊断：

1. `get_workspace_status` 的 `error` 返回后端提取的完整根异常，不返回任意日志尾部；
2. `get_workflow_details(workflow_instance_id)` 获取 Task instance ID；
3. `get_task_logs(..., skip_line_num=0)` 分页读取完整日志；
4. `has_more=true` 时把 `next_line_num` 原样用于下一页。

## 6. 输出

`list_workflow_outputs` 只接受当前 Attempt 且状态为 `SUCCESS` 的 instance。每项包含：

- `name`：API 逻辑输出名；
- `filename`：Parquet 文件名；
- `size`：字节数；
- `modified_at`：UTC 时间；
- `download_url`：需携带相同 Bearer Token 的 REST 下载地址。

Query 默认输出 `data`（文件名 `query.parquet`）；Factor 默认输出 `information_coefficient`、`group_returns`；
Backtest 默认输出见 Backtest 文档。

## 7. 版本

Factor/Backtest 的 `save_version` 只接受当前成功的 `workflow_instance_id`。保存时后端计算并
持久化摘要指标。`list_versions` 返回保存版本和当前未保存草稿；`get_version` 按项目内版本号
读取完整参数、工作流绑定和指标。

## 8. 权限与副作用

- 普通用户只能访问自己的项目、Workspace、Instance、Task 日志和结果；
- `list_*`、`get_*`、文档和 Schema 工具只读；
- `create_project`、三个 `run_*`、`save_version` 会写业务数据；
- `control_workflow` 会改变调度器状态，调用前必须读取当前状态；
- Backtest 在创建 Workspace 前会连接实际 DolphinDB 编译 `utils` 和八个回调，编译失败
  不会产生调度器任务。
