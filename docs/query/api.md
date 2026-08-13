# Query API

本文件只说明 Query 项目的 MCP 操作。请求字段与完整示例见 `arena://docs/query/request`，DSL 见
`arena://docs/overview/dsl`，精确顶层结构见 `arena://schemas/query`。通用 Workspace、Attempt、Task、
日志和控制接口见 `arena://docs/overview/workflows`。

## Query 项目语义

Query 只有 Project，没有 Version。一个项目始终复用一个 Workspace；每次执行新增 Attempt，并更新
项目页当前参数、状态与结果。以前的提交参数从 Attempt 历史读取，旧 Parquet 不保证永久保留。

网页地址：`{ARENA_WEB_URL}/query/projects/{project_id}`。

## 列出、创建与读取项目

```text
list_projects(application="query", page=1, page_size=20)
create_project(application="query", title="项目名称")
get_project(application="query", project_id=1)
```

- `page` 从 1 开始，`page_size` 为 1 到 100。
- `title` 去除首尾空格后长度为 1 到 128。
- `create_project` 的 `result.id` 是 `project_id`。
- `get_project` 的 `current` 表示当前请求与当前 Attempt；Query 不支持 `list_versions`、
  `save_version`、`update_version` 或项目重命名。

## 执行查询

```text
run_query(project_id, request)
```

`request` 必须直接是完整 `FactorQuery`，不能再包一层 `dataset_query`：

```json
{
  "project_id": 1,
  "request": {
    "start_date": "2024-01-01",
    "end_date": "2024-12-31",
    "lookback": "P60D",
    "codes": ["000001.SZ"],
    "factors": ["close"],
    "derivatives": {},
    "filters": []
  }
}
```

服务端先用 Runtime `FactorQuery` 严格校验，再创建当前 Attempt。返回：

```json
{"workspace_id": 73, "workflow_instance_id": 163}
```

Instance ID 在提交阶段可以为 `null`。使用 `get_workspace_status(workspace_id)` 轮询，不要只轮询首次
返回的 Instance。

## 读取当前和以前的请求

当前请求：

```text
get_project("query", project_id).current.parameters
```

以前每次提交的请求：

```text
history = list_workflow_attempts(workspace_id, page=1, page_size=20)
attempt = get_workflow_attempt(history.items[0].attempt_id)
request = attempt.payload.input_json.dataset_query
```

Query 工作流内部输入比 MCP `run_query.request` 多一层 `dataset_query`，这是工作流载荷，不是调用
`run_query` 时应提交的外形。

## 输出

工作流进入 `SUCCESS` 后：

```text
list_workflow_outputs(application="query", workflow_instance_id=current_id)
```

唯一逻辑输出：

| 名称 | 文件 | 内容 |
| --- | --- | --- |
| `data` | `query.parquet` | `time`、`code`、请求的基础字段、派生字段和过滤后的行 |

返回的 `download_path` 是相对 API 路径。将它拼接到 `{ARENA_PUBLIC_URL}` 的 origin，并携带相同
Bearer Token 下载。不能对历史但已不再绑定当前结果的 Instance 假定输出仍可读取。

## 完整调用顺序

```text
1. 读取 query/request、overview/dsl 和 schemas/query
2. describe_dsl_operator 校验每个派生节点
3. create_project("query", title)
4. run_query(project_id, request)
5. get_workspace_status(workspace_id) 直到终态
6. 失败：按 overview/workflows 读取 Attempt、Task 和完整日志
7. 成功：list_workflow_outputs("query", workflow_instance_id)
8. 下载并检查日期、股票域、字段、NULL、行数和过滤结果
```

MCP 不提供 Query 项目或输出删除功能。
