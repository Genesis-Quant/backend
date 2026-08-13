# Arena MCP 工具参考

所有成功结果都位于 `structuredContent.result`。下文中的 ID 必须来自当前用户的真实工具返回值，
不能自行推导。

## 文档与发现

### `read_arena_document(name)`

读取 Markdown 文档。`name` 只能是：

```text
overview | tools | query | factor | backtest | dolphindb-backtest | dsl
```

同名文档也可通过 `arena://docs/{name}` Resource 读取。

### `list_dsl_operators(search="", operator_type=null, limit=50)`

搜索 Runtime 当前注册的基础因子和 DSL 算符。

| 参数 | 约束 |
| --- | --- |
| `search` | 最长 128；匹配算符名或说明，空字符串表示不按文本筛选 |
| `operator_type` | `DIRECT`、`TS`、`CS` 或 `null` |
| `limit` | 1–200 |

返回 `factors`、`operators`、`matched` 和 `returned`。摘要不包含完整字段结构；使用某个算符前
继续调用 `describe_dsl_operator`。

### `describe_dsl_operator(operator)`

输入完整算符名，例如 `binary.gt` 或 `unary.rolling_mean`。返回：

- `op`：完整名称；
- `type`：`DIRECT`、`TS` 或 `CS`；
- `output_kind`：`BOOL`、`NUMBER` 或 `ANY`；
- `description`：语义说明；
- `definition`：该节点的完整 JSON Schema，包括准确的 `fields`、`params`、默认值和额外字段限制。

不存在的算符直接报错。

### `describe_dolphindb_functions(names)`

查询部署中 DolphinDB `defs()` 返回的函数签名。`names` 为 1–100 个简单标识符，例如
`floor`、`fminSLSQP`。不要传 `Backtest::getPosition`、`backtest::order_target` 等带命名空间的
名称；Arena 回测 helper 见 `arena://docs/dolphindb-backtest`。

返回 `requested`、`definitions` 和 `missing`。每个 definition 包含参数数量、syntax 和 DolphinDB
文档 URL；未知或非法名称进入 `missing`，不代表可调用。

## 项目

### `list_projects(application, page=1, page_size=20)`

- `application`：`query`、`factor` 或 `backtest`；
- `page`：从 1 开始；
- `page_size`：1–100。

返回分页对象 `{items,page,page_size,total,limit}`。

### `create_project(application, title)`

创建项目。`title` 去除首尾空格后长度为 1–128。返回完整项目对象，后续使用 `result.id` 作为
`project_id`。Query 项目受每用户最多 5 个的限制。

### `get_project(application, project_id)`

读取项目和当前请求状态：

- Query 当前运行信息位于 `current`；
- Factor/Backtest 当前未保存版本位于 `draft`；
- 服务端规范化后的请求位于对应对象的 `parameters`。

## 提交工作流

三个工具都立即返回 `{workspace_id, workflow_instance_id}`，不会等待调度完成。

### `run_query(project_id, request)`

`request` 必须是完整 `FactorQuery`，不能包成 `dataset_query`。字段见 `arena://docs/query` 和
`arena://schemas/query`。

### `run_factor_analysis(project_id, parameters)`

`parameters` 必须是完整 `FactorAnalysisParameters`。字段见 `arena://docs/factor` 和
`arena://schemas/factor`。

### `run_backtest(project_id, parameters)`

`parameters` 必须是完整 `BacktestParameters`。服务端先在 DolphinDB 中加载 Backtest Runtime，
编译 `utils` 和八个回调；编译成功后才提交调度工作流。编译成功不保证数据读取和运行期逻辑一定
成功。字段见 `arena://docs/backtest`、`arena://docs/dolphindb-backtest` 和
`arena://schemas/backtest`。

## 工作流

### `get_workspace_status(workspace_id)`

提交后首选的轮询接口。返回 Workspace 当前 Attempt 的有效状态、当前
`workflow_instance_id`、错误、事件和时间。重跑后继续用原 `workspace_id` 获取新实例。

### `list_workflows(application=null, state=null, page=1, page_size=20)`

- `application`：`query`、`factor`、`backtest`、`incremental` 或 `null`；
- `state`：`active`、`success`、`failure` 或 `null`；
- `page_size`：1–100。

返回 Workspace 分页列表和当前 Attempt 摘要，不替代具体 Workspace 的状态轮询。

### `get_workflow_details(workflow_instance_id)`

读取一个已有 DolphinScheduler Instance 的定义、输入、requested outputs、状态、事件、耗时和
Task 列表。调度器 Task 查询失败时响应可包含 `tasks_error`；不要在 `tasks` 为空时猜 Task ID。

### `get_task_logs(workflow_instance_id, task_instance_id, skip_line_num=0, limit=1000)`

`limit` 为 1–10000 行。Task 必须属于该工作流。完整读取方法：

```text
skip = 0
repeat:
    page = get_task_logs(..., skip_line_num=skip)
    consume(page.message)
    skip = page.next_line_num
until page.has_more == false
```

`next_line_num` 是绝对行游标，不是本页行数。

### `control_workflow(workflow_instance_id, action)`

有副作用。`action` 只能是：

```text
stop | pause | resume | rerun | retry-failed
```

动作是否允许取决于当前状态。`rerun` 和 `retry-failed` 会创建新 Attempt；动作完成后使用原
`workspace_id` 获取新的当前 Instance。

## 输出

### `list_workflow_outputs(application, workflow_instance_id)`

只读取当前 Attempt 的成功实例。`application` 为 `query`、`factor` 或 `backtest`。返回每个输出：

| 字段 | 含义 |
| --- | --- |
| `name` | 逻辑输出名，下载 API 使用此值 |
| `filename` | 存储中的 Parquet 文件名 |
| `size` | 字节数 |
| `modified_at` | 修改时间 |
| `download_path` | 当前部署的同源 REST 路径，下载仍需 Bearer Token |

固定输出名：

- Query：`data`；
- Factor：`information_coefficient`、`group_returns`；
- Backtest：`trade_details`、`daily_positions`、`daily_portfolios`、
  `daily_trading_statistics`。

## 版本

### `list_versions(application, project_id)`

`application` 只能是 `factor` 或 `backtest`。列出已保存版本和当前未保存版本。

### `get_version(application, project_id, version)`

读取指定版本的完整参数、Workspace/Instance 绑定、保存状态和已持久化摘要。

### `save_version(application, project_id, workflow_instance_id, remark="")`

只接受该项目当前未保存版本的成功工作流。`remark` 最长 512 字符。保存成功后当前版本固定，
项目创建下一未保存版本。

## 常见错误对应位置

| 表现 | 位置 |
| --- | --- |
| HTTP 401 `invalid_token` | MCP 鉴权失败 |
| Tool input validation error | 工具参数或顶层 Runtime Schema 不合法 |
| 未知 op、fields/params 错误、引用环 | DSL 节点不合法 |
| `run_backtest` 直接返回 DolphinDB Syntax Error | utils/callback 编译失败，尚未提交工作流 |
| Workspace `SUBMIT_FAILED` | 调度提交失败，可能没有 Instance |
| Instance `FAILURE` | Worker 运行失败，读取详情和完整 Task 日志 |
| `AUTO_SAVE_FAILED` | 版本保存或摘要处理失败 |
