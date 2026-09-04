# 工作流、Attempt、Task、日志与结果读取

本文件定义 Query、Factor、Backtest 共用的异步执行与诊断 API。如何创建和运行某类项目、该应用
有哪些输出、怎样保存版本或创建专项研究，分别见该应用目录下的 `api` 文档。

对象关系见 `arena://docs/overview/projects`。以下所有工具都会按当前 Bearer Token 鉴权。

## 从提交结果开始轮询

业务 `run_*` 工具返回：

```json
{
  "workspace_id": 73,
  "workflow_instance_id": 163
}
```

`workflow_instance_id` 在尚未提交到 DolphinScheduler 时可以是 `null`。始终保存并轮询
`workspace_id`：

```text
get_workspace_status(workspace_id)
```

该工具返回 Workspace 当前 Attempt 的提交状态、有效状态、当前 Workflow Instance、错误与事件。
重跑后 Instance ID 会变化，因此不要把 `get_workflow_status(old_id)` 当作 Workspace 当前状态。

## 状态语义

| 状态 | 含义 | 调用方行为 |
| --- | --- | --- |
| `QUEUED`、`CREATED`、`SUBMITTING`、`SUBMITTED` | 创建或提交中 | 继续轮询 Workspace |
| `RETRYING` | DolphinScheduler 正在原 Instance 内启动失败 Task 续跑 | 继续轮询 Workspace |
| DolphinScheduler 非终态，如 `RUNNING_EXECUTION` | 调度执行中 | 继续轮询 Workspace |
| `RESULT_PENDING` | 调度已成功，必需输出尚未完成校验 | 继续轮询 Workspace |
| `SUCCESS` | 调度成功且必需输出已通过校验 | 按应用 API 文档读取输出或保存版本 |
| `AUTO_SAVE_PENDING` | 批量任务成功，自动保存版本中 | 继续轮询，不能停止已结束实例 |
| `SUBMIT_FAILED` | 未成功提交调度器 | 读取 Attempt 错误；不能用 Instance 控制工具 |
| `FAILURE`、`AUTO_SAVE_FAILED`、`RESULT_FAILED` | 执行、后处理或结果校验失败 | 读取 Attempt、Task 与完整日志 |
| `STOP`、`KILL` | 已终止 | 不读取输出 |

有 Workflow Instance 时，调度状态和提交/自动保存状态仍是不同层次。`AUTO_SAVE_PENDING` 与
`AUTO_SAVE_FAILED` 不能被已完成实例的 `SUCCESS` 覆盖。

## 工作流面板

```text
list_workflows(application=null, state=null, page=1, page_size=20)
```

- `application`：`query`、`factor`、`backtest`、`incremental` 或 `null`。
- `state`：`active`、`success`、`failure` 或 `null`。
- `page_size`：1 到 100。

返回值按 Workspace 分页，每个 Workspace 只携带当前 Attempt，不会把历史 Attempt 平铺成重复行。
未产生 Attempt 的草稿不显示；尚无 Workflow Instance 的提交中或提交失败 Attempt 仍会显示。

## Workspace 的运行历史

```text
list_workflow_attempts(workspace_id, page=1, page_size=20, include_tasks=false)
get_workflow_attempt(attempt_id)
```

历史按创建时间倒序，`page_size` 为 1 到 50。摘要包含 `attempt_id`、`is_current`、状态、Instance ID
和时间；详情包含本次提交后由 Backend 规范化并保存的 `payload.input_json`、requested outputs、
错误、状态历史和生命周期事件。
`include_tasks=false` 时不会为分页中的每条 Attempt 请求 DolphinScheduler；需要批量读取
Task 摘要时才设为 `true`。诊断某一次运行时应优先使用 `list_workflow_tasks`。

读取以前提交参数时必须读 Attempt：当前项目或未保存版本的 parameters 会被下一次运行更新，历史
Attempt 的 `payload.input_json` 不会。它是 Backend `stored_payload` 生成的不可变业务请求，可能包含
请求携带的双源码和结构化字段；Backend 不生成、格式化、迁移或互相转换源码，三项 `dsl_source`
字段与提交内容逐字一致。Query 的业务请求位于 `payload.input_json.dataset_query`；Factor/Backtest
的 `input_json` 是完整应用参数。

不要把数据库字段 `WorkflowAttempt.input_json` 与共享目录文件 `input.json` 混为一谈：

| 对象 | 内容 | 用途与保留边界 |
| --- | --- | --- |
| `WorkflowAttempt.input_json` | 双源码、活动 `language` 及规范化业务字段 | 每个 Attempt 独立保存，用于回显、审计和重新构造执行参数 |
| `<shared-dir>/<application>/<workspace_key>/input.json` | 提交前重新编译并移除 `dsl_source` 的 Runtime 纯 JSON | Worker 通过 `--input-file` 读取；同一 Workspace 再次运行时会重写，不是历史记录 |

完整的 DSL 编译与托管节点合并顺序见 `arena://docs/overview/dsl` 的“从 MCP 源码到 Runtime JSON”。

## Workflow Instance 信息

```text
get_workflow_status(workflow_instance_id)
get_workflow_details(workflow_instance_id)
list_workflow_tasks(workflow_instance_id)
```

- `get_workflow_status`：同步并返回轻量状态。
- `get_workflow_details`：返回调度定义、提交参数、事件和 Task 摘要；旧定义已不存在时仍应返回详情，
  `task_count` 可以为 0。
- `list_workflow_tasks`：按需读取当前 Task instances，适合详情面板或故障诊断。

只有 Attempt 已绑定 Workflow Instance 后才能调用这些接口。历史实例是否仍有调度详情取决于
DolphinScheduler 的数据保留周期。

## Task 日志分页与下载

`get_workspace_status`、`get_workflow_attempt` 和 `get_workflow_details` 返回的是结构化状态、事件和 Task
摘要，不是 Worker 的完整执行日志。真正的 stdout、DOS 输出、警告和异常按 Task 保存；先定位
`task_instance_id`，再读取该 Task 的日志。一个 Workflow 有多个 Task 时，不存在可替代全部 Task
日志的单一“工作流摘要日志”。

先从 `list_workflow_tasks` 或 `get_workflow_details` 找到 `task_instance_id`，再读取：

```text
get_task_logs(
  workflow_instance_id,
  task_instance_id,
  skip_line_num=0,
  limit=1000,
  scope="full",
  cursor=null
)
```

- `limit` 为 1 到 10000 行，不是字节上限。
- `scope="full"` 是默认值，返回完整 DolphinScheduler 调度日志，与原有行为一致。
- `scope="worker"` 只返回 Worker 子进程的 stdout/stderr，包括 Runtime、Loguru、DOS 输出和异常，
  省略任务初始化、环境和脚本内容等调度上下文。它适合快速检查实际计算过程。
- 首次从 `skip_line_num=0` 开始。
- 当 `has_more=true` 时，把 `next_line_num` 原样作为下一页的 `skip_line_num`。
- `skip_line_num`、`next_line_num` 和 `returned_lines` 都按当前 `scope` 的可见行计数；切换范围后必须
  从 0 重新分页，不能混用两个范围的游标。
- `scope="worker"` 时，后续页和实时增量读取还应把上次响应的 `next_cursor` 作为 `cursor` 传回；它
  保存原始 DolphinScheduler 行位置和跨页子进程段状态，避免每次从完整日志开头重扫。旧调用方省略
  `cursor` 仍能按 `skip_line_num` 得到正确内容，但长日志效率较低。`full` 范围不使用该字段。
- 直到 `has_more=false` 才是完整日志；不能只根据第一屏尾部猜测根因。
- 正在创建或尚未产生 stdout/stderr 的 Task，Worker 范围会暂时返回 0 行；继续从 0 刷新即可读取
  随后产生的输出。

需要一次下载完整日志时：

```text
get_task_log_download(workflow_instance_id, task_instance_id)
```

返回 `download_path`。下载始终是未经筛选的完整日志；Worker-only 范围通过 `get_task_logs` 分页读取。
将下载路径拼接到 `{ARENA_PUBLIC_URL}` 的 origin，并携带同一 Bearer Token。Task 必须
属于指定 Workflow Instance，服务端会同时校验两者的所有权。

### DolphinDB/DOS 输出已进入 Task 日志

Runtime 创建 DolphinDB Session 后会关闭 SDK 默认 stdout sink，并把 `session.msg_logger` 接到统一
Loguru sink。Query、Factor 和 Backtest 执行前都会启用该重定向，因此当前 Session 中 DOS 脚本的
`print(...)` 输出、DolphinDB 服务端消息、警告和异常会随 Python Worker 输出进入 DolphinScheduler
Task 日志，而不是只显示在 DolphinDB 客户端终端。

这意味着可以在 DSL 执行脚本、Backtest `utils` 或生命周期 callback 中加入少量 `print(...)`，然后
通过 `get_task_logs` 分页查看，用于确认代码是否进入某个分支、回调日期、信号数量、订单号和关键
中间值。需要完整保存时使用 `get_task_log_download`。

日志仅用于调试，不是结构化结果：不要在日志中输出 Token、密码或完整大表，不要依赖日志文本生成
交易报告，也不能用一条“提交订单”日志代替 `onOrder`、`onTrade` 和 Parquet 对账。大量逐行打印会
显著增加 Task 日志体积和调度开销，定位完成后应删除或降到必要摘要。

## 通用失败诊断顺序

```text
1. get_workspace_status(workspace_id)：读取 error、events、当前 instance
2. get_workflow_attempt(attempt_id)：读取原始输入和提交事件
3. list_workflow_tasks(workflow_instance_id)：定位失败 task_instance_id
4. get_task_logs(..., skip_line_num=0)：分页读到 has_more=false
5. 对照目标应用 request/api 文档与 Runtime Schema 判断是请求、数据、脚本还是后处理问题
```

不要在没有读取完整日志时修改参数重试；否则会丢失首次失败的诊断价值。

## 输出元数据和下载

```text
list_workflow_outputs(application, workflow_instance_id)
```

`application` 为 `query`、`factor` 或 `backtest`。Instance 必须成功且仍绑定当前业务结果。每个输出
返回逻辑名称、文件名、大小、修改时间和 `download_path`，不内嵌 Parquet 数据。具体输出名称、
业务语义和读取方式见对应应用的 `api` 文档。

下载时把相对 `download_path` 拼接到 `{ARENA_PUBLIC_URL}` 的 origin，并使用同一 Bearer Token。不要把
一个部署环境签发的 Token 发送到另一个域名。旧 Attempt 的 Instance 不等于永久输出归档；同一
Workspace 的旧结果可能已被当前结果覆盖。

### 认证下载示例

以下示例中的 `DOWNLOAD_PATH` 必须使用 `list_workflow_outputs` 或 `get_task_log_download` 当前返回的
相对路径，不要自行猜文件 URL。

PowerShell：

```powershell
$baseUrl = "{ARENA_PUBLIC_URL}"
$downloadPath = $env:DOWNLOAD_PATH
$headers = @{ Authorization = "Bearer $env:ARENA_TOKEN" }
Invoke-WebRequest -Uri "$baseUrl$downloadPath" -Headers $headers -OutFile .\arena-output.parquet
```

curl：

```bash
ARENA_BASE_URL="{ARENA_PUBLIC_URL}"
curl --fail --location \
  --header "Authorization: Bearer ${ARENA_TOKEN}" \
  "${ARENA_BASE_URL}${DOWNLOAD_PATH}" \
  --output arena-output.parquet
```

Python 标准库：

```python
import os
import shutil
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

base_url = "{ARENA_PUBLIC_URL}"
download_path = os.environ["DOWNLOAD_PATH"]
download_url = urljoin(f"{base_url.rstrip('/')}/", download_path)
arena = urlsplit(base_url)
arena_origin = (arena.scheme.lower(), arena.hostname, arena.port)
initial = urlsplit(download_url)
if (initial.scheme.lower(), initial.hostname, initial.port) != arena_origin:
    raise ValueError("DOWNLOAD_PATH 必须指向当前 Arena API")


class ArenaRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        target_url = urljoin(request.full_url, new_url)
        source = urlsplit(request.full_url)
        target = urlsplit(target_url)
        if target.scheme not in {"http", "https"}:
            raise ValueError("下载重定向只允许 HTTP(S)")
        if source.scheme == "https" and target.scheme != "https":
            raise ValueError("拒绝从 HTTPS 降级到 HTTP")
        redirected = super().redirect_request(
            request, file, code, message, headers, target_url
        )
        target_origin = (target.scheme.lower(), target.hostname, target.port)
        if redirected is not None and target_origin != arena_origin:
            redirected.remove_header("Authorization")
        return redirected


request = Request(
    download_url,
    headers={"Authorization": f"Bearer {os.environ['ARENA_TOKEN']}"},
)
opener = build_opener(ArenaRedirectHandler())
with opener.open(request) as response, Path("arena-output.parquet").open("wb") as target:
    shutil.copyfileobj(response, target)
```

Token 只能发送到当前 Arena API 的 origin。Arena 可以返回指向对象存储的跨 origin 预签名
重定向；这种重定向是正常的，但后续请求绝不能携带 `Authorization` header。上述 Python 示例
会校验初始 URL，允许安全的预签名跨 origin 重定向，并在离开 Arena origin 前移除 Token。

### 保留与本地归档

- Query 重复运行会更新当前结果，旧 Parquet 不保证继续可下载；每个用户最多 5 个 Query 项目；
- Factor/Backtest 已保存版本固定其业务结果绑定，但对象存储、DolphinScheduler 日志和数据库仍受
  部署方保留策略约束，不能把在线地址当成永久档案；
- Attempt 保留的是提交参数、状态和事件，不等于保留该次 Parquet；
- 调度器返回 `SUCCESS` 后，Backend 还会检查所有必需输出是否存在、非空且包含完整 Parquet
  文件尾；任何请求输出缺失、为空或不完整都会记为 `RESULT_FAILED`；
- 对象存储或本地文件系统暂时不可用时保持 `RESULT_PENDING` 并重试，只有确定缺失、
  为空或不完整才记录 `RESULT_FAILED`；旧的未校验 `SUCCESS` 会在列表、状态、结果或
  保存版本首次访问时惰性校验；
- `list_workflow_outputs` 只对仍绑定当前业务结果且通过输出校验的成功 Instance 提供文件元数据。

需要复现时，建议在结果仍可下载时一并归档：另行保存的 MCP 原始请求或 Attempt 中的规范化
`payload.input_json`、Project/Version/Workspace/Attempt/Workflow ID、所有输出文件、文件大小与哈希、
Runtime/Backend 版本、基础
数据版本、费用与年化参数。不要只保存截图或摘要指标。

## 工作流控制

```text
control_workflow(workflow_instance_id, action)
```

`action` 可为：

- `stop`：停止仍在运行的实例；
- `pause`：请求暂停调度。DolphinScheduler 不会中断已经开始运行的 Task；状态可能先进入
  `READY_PAUSE`，等待当前 Task 结束后才进入 `PAUSE`。只有一个 Task 的工作流可能在等待期间直接
  完成并进入 `SUCCESS`；需要立即终止计算时应使用 `stop`；
- `resume`：仅恢复已进入 `PAUSE` 的工作流。`READY_PAUSE` 是 DolphinScheduler
  等待当前 Task 结束的中间状态，调度器不接受对该状态执行恢复；
- `rerun`：使用原 Attempt 的完整输入重新提交整个工作流，创建新的 Attempt 和新的 Workflow
  Instance；旧 Attempt/Instance 保留为历史记录。Incremental 会复用上一 Attempt 已校验的 workers、
  channel 和 overwrite 参数；
- `retry-failed`：让 DolphinScheduler 在原 Workflow Instance 内续跑失败 Task，不创建新的 Attempt
  或 Instance；状态和 Task 列表仍通过原 Workspace/Instance 查询。

提交中但没有 Instance 的 Workspace 不能调用该工具。重跑后回到 `get_workspace_status(workspace_id)`
获取新的当前 Instance。

高级 Task 控制：

```text
force_success_task(workflow_instance_id, task_instance_id)
```

它会改变真实调度状态，只应在用户明确要求、已经确认 Task 可安全跳过时调用。Task 必须属于该
Workflow Instance。强制成功只改变 Task 状态；失败工作流仍需调用 `retry-failed` 才能继续。即使
调度器随后成功，Backend 仍会校验必需输出，跳过产出结果的 Task 会得到 `RESULT_FAILED`，不能保存
版本或读取输出。MCP 不提供删除工作流实例、Attempt、Task 或输出的功能。用户按个人主页权限删除
项目、版本或回测分析时，应调用对应应用文档列出的专用工具；这些工具会按业务关系清理其独占
Workspace 和产物。
