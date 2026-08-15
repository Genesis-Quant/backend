# Project、Version、Workspace、Attempt 与 Workflow Instance

本文件解释 Arena 三类项目共用的对象关系、生命周期和历史保留语义。业务工具参数和输出名称不在
这里重复：Query 见 `arena://docs/query/api`，Factor 见 `arena://docs/factor/api`，Backtest 见
`arena://docs/backtest/api`。

## 对象关系

```text
User
  └─ Project
      ├─ Query: 一个 Project 直接对应一个 Workspace
      └─ Factor / Backtest
          └─ Version（已保存或当前未保存）
              └─ 一个 Workspace
                  └─ Attempt 1..N
                      └─ Workflow Instance 0..1
                          └─ Task Instance 0..N
```

| 对象 | 含义 | 是否复用 | 标识 |
| --- | --- | --- | --- |
| Project | 用户在项目列表创建和打开的业务对象 | 保留 | `project_id` |
| Version | Factor/Backtest 的一份研究版本 | 已保存后固定；未保存版本可更新 | 项目内 `version` |
| Workspace | 项目或版本的稳定执行容器 | 再次运行、重跑时复用 | `workspace_id` |
| Attempt | 一次提交、重跑或失败续跑 | 每次创建新记录 | `attempt_id` |
| Workflow Instance | Attempt 成功提交到 DolphinScheduler 后的调度实例 | 不复用 | `workflow_instance_id` |
| Task Instance | Workflow Instance 内的具体调度任务 | 不复用 | `task_instance_id` |

`project_id` 不是运行 ID，`workspace_id` 也不是 DolphinScheduler Instance ID。运行过程中应按
Workspace 获取当前 Attempt；只有读取某次调度详情、Task、日志或结果时才使用 Workflow Instance。

## Query 的对象关系

Query 只有 Project，没有 Version：

```text
Query Project ──1:1── Workspace ──1:N── Attempt
```

修改 DSL 再次执行时，Project 和 Workspace 不变，新增 Attempt。项目页的当前参数、状态和结果更新
为当前 Attempt；旧 Attempt 的输入、状态和事件仍可读取。Query 没有保存版本能力，因此需要长期保存
某次 Parquet 时，应在下一次运行前下载。

完整操作和输出见 `arena://docs/query/api`。

## Factor 与 Backtest 的对象关系

创建 Factor/Backtest Project 时会立即创建一个未保存 Version，例如 `v1`：

```text
version = 1
saved = false
is_current = true
```

未保存不等于不存在。它有固定版本号和一对一 Workspace，可以运行、展示和参与对比；继续修改并运行
仍复用这一个 Version/Workspace，只新增 Attempt，参数和当前结果更新。

当前 Attempt 成功后可把该 Version 保存。保存后：

1. 当前 Version 变为 `saved=true`，绑定保存时的参数、成功 Instance、摘要与结果；
2. 项目自动创建下一个未保存 Version；
3. 新 Version 使用新的 Workspace，并从上一版本参数继续研究；
4. 后续运行不会修改已保存 Version。

推荐顺序是“运行当前草稿 → 核验当前 Instance 的输出 → 保存该 Instance”。保存后继续工作时使用系统
自动创建的新草稿，不要把旧 Workspace 或旧 Instance 当作新草稿的运行标识。

Factor 与 Backtest 的保存、重命名、批量自动保存和专项研究 API 分别在各自 `api` 文档中说明。

## Workspace 和 Attempt 为什么分开

Workspace 表示“这个项目或版本的执行位置”，Attempt 表示“这一次具体尝试”。以下操作都会在原
Workspace 创建新 Attempt：

- 用户修改参数后再次执行；
- 使用相同输入完整重跑；
- 失败续跑；
- 提交失败后的安全重试。

新 Attempt 可以还没有 Workflow Instance，例如 `QUEUED`、`SUBMITTING` 或 `SUBMIT_FAILED`。
Attempt 一旦成功绑定调度实例，才有 `workflow_instance_id` 和 Task 信息。

## 当前状态与历史状态

- `get_project` / `get_version`：读取页面当前展示的参数、状态与结果绑定。
- `get_workspace_status`：读取 Workspace 当前 Attempt，适合轮询。
- `list_workflow_attempts`：分页读取同一 Workspace 的当前和历史 Attempt。
- `get_workflow_attempt`：读取一次提交的完整 `payload.input_json`、输出请求、错误和事件。
- `get_workflow_details` / `list_workflow_tasks`：读取某个已产生的 Workflow Instance。

以前每次提交的参数必须从 Attempt 读取。当前未保存 Version 的 parameters 会被下一次执行更新，而
历史 Attempt 的 `payload.input_json` 保持不变。Query 的业务请求位于
`payload.input_json.dataset_query`；Factor/Backtest 的 `input_json` 是完整 parameters。

## 结果与历史的保留边界

| 内容 | 同一 Workspace 再次运行 | 保存 Factor/Backtest Version 后 |
| --- | --- | --- |
| Project / Version / Workspace | 保留 | 已保存 Version 与 Workspace 固定 |
| Attempt 输入、状态、事件 | 每次新增并保留 | 各 Version 分别保留 |
| 页面当前参数与结果绑定 | 更新为当前 Attempt | 已保存 Version 固定，新草稿独立更新 |
| 旧 Workflow/Task 日志 | 受调度器保留周期影响 | 同样受调度器保留周期影响 |
| 同一 Workspace 的旧 Parquet | 不保证继续可下载 | 保存版本的结果不被新草稿覆盖 |

Attempt 历史是参数与状态审计记录，不是每次 Parquet 的永久归档。`list_workflow_outputs` 只接受仍然
绑定业务结果的成功 Instance。

## 项目页面

| 应用 | 地址 |
| --- | --- |
| Query | `{ARENA_WEB_URL}/query/projects/{project_id}` |
| Factor | `{ARENA_WEB_URL}/factor/projects/{project_id}` |
| Backtest | `{ARENA_WEB_URL}/backtest/projects/{project_id}` |
| 工作流面板 | `{ARENA_WEB_URL}/workflows` |

页面路由只使用 `project_id`。不要填入 Workspace、Attempt 或 Workflow Instance ID。Factor/Backtest
页面默认打开最新已保存版本；没有已保存版本时打开当前未保存版本，其他版本在页面内切换。浏览器必须
登录同一用户，管理员之外不能越权打开其他用户项目。

## 运行、重跑与失败续跑

| 操作 | 输入来源 | 对象变化 |
| --- | --- | --- |
| 再次调用业务 `run_*` | 新提交的完整请求 | 原 Workspace 新增 Attempt |
| `control_workflow(..., "rerun")` | 原 Attempt 输入 | 原 Workspace 新增 Attempt 和 Instance |
| `control_workflow(..., "retry-failed")` | 原 Attempt 输入 | 原 Workspace 新增 Attempt 和 Instance |

重跑后旧 `workflow_instance_id` 不再代表当前运行。始终保留 `workspace_id` 并重新读取当前 Instance。
通用轮询、历史、日志、输出和控制 API 见 `arena://docs/overview/workflows`。
