# Arena MCP 总览

Arena MCP 把网页中的 Query、Factor 和 Backtest 能力提供给 AI。它支持项目创建与读取、异步工作流
提交、Workspace 轮询、运行历史、Task 与日志、结果下载，以及 Factor/Backtest 的版本和批量研究。
本文件只介绍通用能力和入口；业务请求字段、专用工具和输出必须读取对应应用目录中的文档。

## 连接与认证

- Endpoint：`{ARENA_PUBLIC_URL}/mcp`
- 网页入口：`{ARENA_WEB_URL}`
- MCP 页面：`{ARENA_WEB_URL}/mcp`，与 MCP Resources 读取同一组 Markdown
- Transport：MCP Streamable HTTP，无状态模式
- 每个 HTTP 请求都必须携带 `Authorization: Bearer <access_token>`
- MCP 不提供登录、注册、用户管理或业务对象删除工具；认证用户可使用只读 DolphinDB 诊断工具

Token 由 REST 登录接口签发：

```http
POST {ARENA_PUBLIC_URL}/api/v1/auth/login
Content-Type: application/json

{"username":"your_username","password":"your_password"}
```

HTTP 401 `invalid_token` 表示 Token 缺失、无效或过期。`get_current_user()` 可确认当前 Token 对应的
用户。项目、Workspace、Attempt、Workflow Instance、Task、日志与输出都按该用户鉴权。

## 返回值契约

所有 MCP 工具的业务结果位于：

```text
CallToolResult.structuredContent.result
```

例如 `create_project` 返回的项目 ID 是 `structuredContent.result.id`。不要解析 `content[].text`，也
不要把 `structuredContent` 本身当作业务对象。`isError=true` 表示工具校验或同步执行失败；`run_*`
成功只代表任务已提交，不能据此判断异步工作流成功。

## 文档导航

| 目的 | 必须读取的 Resource |
| --- | --- |
| 项目、版本、Workspace、Attempt、Workflow Instance 关系 | `arena://docs/overview/projects` |
| DSL 节点、依赖、过滤与算符发现 | `arena://docs/overview/dsl` |
| 执行只读 DolphinDB 测试脚本 | `arena://docs/overview/dolphindb` |
| 工作流状态、运行历史、Task、日志、输出和控制 | `arena://docs/overview/workflows` |
| Query 请求与 API | `arena://docs/query/request`、`arena://docs/query/api`、`arena://schemas/query` |
| Factor 请求与 API | `arena://docs/factor/request`、`arena://docs/factor/api`、`arena://schemas/factor` |
| Backtest 请求与 API | `arena://docs/backtest/request`、`arena://docs/backtest/api`、`arena://schemas/backtest` |
| Backtest 行情、回调和撮合 | `arena://docs/backtest/dolphindb` |
| Backtest 插件函数白名单 | `arena://docs/backtest/interfaces` |
| Backtest 四张结果表、费用和审计 | `arena://docs/backtest/results` |
| 动态数据域、两阶段查询与时点边界 | `arena://docs/backtest/dynamic-pool` |
| 二次规划接口、数值处理与调仓顺序 | `arena://docs/backtest/optimization` |
| 回调、持仓、订单、成交与拒单诊断边界 | `arena://docs/backtest/callback-data` |
| 运行、结果 QA 与保存版本顺序 | `arena://docs/backtest/qa` |

使用 `read_arena_document(name)` 可读取同名文档；`name` 与上述 `arena://docs/*` 路径一致，例如
`overview/workflows`。Schema 定义顶层业务对象，某个 DSL 节点的精确 `fields`、`params` 和 `on`
约束必须以 `describe_dsl_operator(operator)` 返回的定义为准。

## 发现能力

- `read_arena_document(name)`：读取本文档表中的 Markdown。
- `list_dsl_operators(search="", operator_type=null, limit=50)`：搜索基础字段和算符。
- `describe_dsl_operator(operator)`：读取一个算符的精确节点 Schema。
- `describe_dolphindb_functions(names)`：查询 DolphinDB 内置函数签名和官方文档链接；Arena 回测
  helper 在 `arena://docs/backtest/dolphindb` 中说明。
- `execute_dolphindb_script(script, max_rows=200)`：所有认证用户可用，使用只读运行账号在共享
  DolphinDB 中执行测试脚本；其无计算资源沙箱和超时边界必须先读 `arena://docs/overview/dolphindb`。
- `get_current_user()`：读取当前认证用户。

发现结果不是执行结果。先完成发现和请求校验，再创建项目并运行。

## 项目的通用入口

```text
list_projects(application, page=1, page_size=20, search=null, sort_by="updated_at", sort_order="desc")
create_project(application, title)
get_project(application, project_id)
```

`application` 是 `query`、`factor` 或 `backtest`。`list_projects` 每页 1 到 100 条，`search` 按项目名称
或 ID 片段过滤，`sort_order` 为 `asc` 或 `desc`；三种应用允许的 `sort_by` 不同，必须读取对应应用
API 文档。`title` 去除首尾空格后长度为 1 到 128。Factor/Backtest 的重命名、版本和批量能力也是
业务专用能力，不在本页展开。

项目页面地址：

| 应用 | 地址 |
| --- | --- |
| Query | `{ARENA_WEB_URL}/query/projects/{project_id}` |
| Factor | `{ARENA_WEB_URL}/factor/projects/{project_id}` |
| Backtest | `{ARENA_WEB_URL}/backtest/projects/{project_id}` |
| 工作流面板 | `{ARENA_WEB_URL}/workflows` |

页面只接受 `project_id`，不要把 `workspace_id`、`attempt_id` 或 `workflow_instance_id` 填入项目路由。
浏览器必须登录同一用户。

## 通用执行流程

```text
1. 读取本页、对象关系、工作流文档和目标应用的 request/api/Schema
2. 发现并校验所有 DSL 算符，按 overview/dsl 将单次使用的中间节点尽量嵌套并保持最小输出列；
   Backtest 还要核对 DolphinDB 回测契约和插件函数白名单
3. create_project 或 get_project
4. 调用目标应用的 run 工具，保存 workspace_id
5. get_workspace_status(workspace_id) 轮询当前 Attempt
6. 失败时读取 Attempt、Workflow、Task 和完整日志
7. 成功后按目标应用 API 文档读取输出
8. Factor/Backtest 需要固化时保存版本
```

Workspace 是稳定的业务执行容器，重跑会创建新的 Attempt 和新的 Workflow Instance。因此持续轮询
`workspace_id`，再从当前状态取得最新 `workflow_instance_id`；不要一直轮询旧实例。

## 结果可信度

工作流 `SUCCESS` 只说明程序成功完成，不证明业务结果正确：

- Query/Factor 要检查日期、股票域、缺失值、派生依赖、过滤结果和输出行数。
- Backtest 要检查信号时序、价格尺度、订单状态、成交覆盖率、拒单、费用、现金、持仓、期末挂单和
  指标口径；执行契约见 `arena://docs/backtest/dolphindb`，结果契约见
  `arena://docs/backtest/results`。
- 输出下载前先用目标应用 API 文档确认逻辑输出名称。

## 权限与副作用

普通用户只能访问自己的资源。创建项目、提交工作流、保存/重命名版本、批量研究及工作流控制会改变
状态；查询工具只读。MCP 不提供项目、版本、工作流或研究的专用删除工具。任意 DolphinScript
工具使用只读数据库账号，持久化写入、删改和管理操作会被 DolphinDB 拒绝；但脚本仍可能消耗大量
CPU、内存和执行时间，调用前必须按其独立资源边界处理。
