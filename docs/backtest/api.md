# Backtest API

本文件说明策略回测的项目、编译提交、版本、批量执行、专项研究和输出 API。请求对象见
`arena://docs/backtest/request`，回调、行情、价格尺度、撮合、订单簿和事件见
`arena://docs/backtest/dolphindb`，四张结果表、费用、订单状态和对账见
`arena://docs/backtest/results`，Schema 见 `arena://schemas/backtest`。通用工作流诊断见
`arena://docs/overview/workflows`。

运行、失败诊断、输出 QA 和保存顺序见 `arena://docs/backtest/qa`；动态数据域、二次规划与目标权重、
回调对象契约分别见 `arena://docs/backtest/dynamic-pool`、`arena://docs/backtest/optimization`、
`arena://docs/backtest/callback-data`。这些资源不提供具体策略或构造。

## 项目与版本

创建 Backtest 项目会创建当前未保存版本和一对一 Workspace。反复运行更新该未保存版本；保存成功
后固化参数、结果和摘要，并创建下一未保存版本。

网页地址：`{ARENA_WEB_URL}/backtest/projects/{project_id}`。

```text
list_projects("backtest", page=1, page_size=20)
create_project("backtest", title)
get_project("backtest", project_id)
update_project("backtest", project_id, title)
```

`update_project` 只改标题。

## 执行一次回测

```text
run_backtest(project_id, parameters)
```

`parameters` 必须直接是完整 `BacktestParameters`，不能只传修改部分。至少应包含合法
`dataset_query` 和八个固定名称 callbacks；完整外形见 request 文档。

调用分两步：

1. Runtime 模型严格校验参数，并在 DolphinDB 中编译 `utils` 与八个回调；
2. 编译成功后才创建 Attempt 并提交调度器。

编译错误会让 MCP 工具直接 `isError=true`，此时没有 Workspace。提交成功返回 `workspace_id` 与可能
暂为空的 `workflow_instance_id`；之后按 Workspace 轮询。

## 版本读取与保存

```text
list_versions("backtest", project_id)
get_version("backtest", project_id, version)
save_version("backtest", project_id, workflow_instance_id, remark="")
update_version("backtest", project_id, version, remark)
```

- 列表包含已保存版本和当前未保存版本。
- 版本详情包含完整 parameters、Workspace/Instance、summary、保存状态和备注。
- 只能保存当前未保存版本绑定的当前成功 Instance；保存后自动创建下一未保存版本。
- `update_version` 只修改显示备注。

## 普通批量执行

```text
run_backtest_batch(project_id, items)
```

`items` 为 1 到 100 项。每项必须包含唯一 `client_id`、可选 `remark`、完整
`BacktestParameters parameters`，不是局部 override：

```json
{
  "project_id": 1,
  "items": [
    {"client_id": "queue-001", "remark": "参数 A", "parameters": {"...": "完整请求"}}
  ]
}
```

提交时不会预占版本号；每个成功项完成输出校验和摘要收集后，才自动保存为下一个未使用的递增版本，
失败项不占号。并发项按成功保存顺序编号，不保证与 `items` 顺序一致；应分别轮询返回的 Workspace，并用
`client_id` 识别具体项。重试同一批次时复用原 `client_id`。服务端会先校验全部参数并逐项完成
DolphinDB 脚本编译，全部通过后才创建 Workspace；
任一项编译失败时整批不提交。工具调用即提交，不存在 MCP 侧本地队列。

同一 `client_id` 已在排队或运行时只返回原 Workspace，不重复提交；提交结果不确定时，新 Attempt
先用原 job marker 对账调度器，确认未创建 Instance 后才重新提交；已有 Workflow Instance 明确失败
时，新 Attempt 使用新的 job marker 完整重跑；仅自动保存失败时只重试结果收集和版本保存，不重复
执行 Backtest 任务。

## 手续费与参数敏感性研究

专项研究基于一个已保存 Backtest 版本。一次研究只创建一条 `BacktestResearch`、一个
`sensitivity` Workspace 和一个工作流实例；全部手续费率或参数组合在该工作流的同一 DolphinDB
session 中运行，完整区间数据和合成消息表只生成一次。

### 列出研究

```text
list_backtest_researches(
  page=1,
  page_size=20,
  project_id=null,
  version=null,
  analysis_type=null
)
```

`analysis_type` 为 `fee_analysis`、`sensitivity` 或 `null`；`page_size` 为 1 到 100。

### 创建通用研究

```text
create_backtest_research(
  analysis_type,
  project_id,
  version,
  parameter_sets,
  description=""
)
```

`version` 必须是已保存版本；`parameter_sets` 为 1 到 100 份完整 BacktestParameters。服务端不会把
局部字典合并进基准参数，因此调用方必须先读取基准 `get_version(...).parameters`，在本地生成每个
完整请求，再提交。参数敏感性分析只允许各请求的 `params` 不同，手续费分析只允许
`config.commission` 不同；其余数据查询、代码和回测配置必须与来源版本一致。校验通过后只创建一个
研究 Workspace。

### 创建手续费分析

```text
create_backtest_fee_analysis(project_id, version, rates)
```

`rates` 为 1 到 100 个 `[0, 1]` 内费率，服务端去重排序，并从基准版本生成完整请求。

### 读取研究结果

```text
get_backtest_research(research_id)
list_backtest_research_outputs(research_id)
```

轮询返回的唯一 `workflow_workspace_id`；运行中可通过通用 Workspace、Attempt、Task 和日志工具查看
进度。工作流成功后，Runtime 已经生成 `results.parquet`，不再需要调用后端二次计算接口。网页通过
研究输出接口下载该文件，并用 DuckDB 读取每个 case 的参数、状态、错误和指标。单个 case 失败会以
`status=FAILURE` 行保留；至少一个 case 成功时工作流仍可成功，因此必须检查每一行的 `status`。
`list_backtest_research_outputs` 在成功后返回固定的 `results` 输出及认证下载路径。

Backend 会在工作流成功后校验 `results.parquet` 的行数、`case_index` 完整性和逐行 `status`，再把
`completed_count`、`failed_count` 与当前 workflow instance 绑定。校验完成前研究状态为
`RESULT_PENDING`；读取或结构校验失败时为 `RESULT_FAILED` 并在 `error` 返回原因。重跑 Workspace
产生新 Attempt 后，旧实例的计数不会用于新实例。即使全部 case 都失败，Runtime 仍保存逐行错误，
工作流可以成功且 `completed_count=0`，因此业务有效性必须以这两个计数和结果行状态为准。

## 滚动参数调优

参数调优同样基于已保存 Backtest 版本，每份报告对应一个 `optimization` Workspace。可用工具：

```text
list_backtest_optimizations(project_id, version, page=1, page_size=20)
create_backtest_optimization(
  project_id,
  version,
  parameter_space,
  algorithms,
  start_date,
  end_date,
  lookback_period,
  holding_period,
  repetitions=1,
  evaluation_budget=12,
  seed=20260815
)
get_backtest_optimization(optimization_id)
list_backtest_optimization_outputs(optimization_id)
```

`parameter_space` 只能引用来源版本 `params` 中已有的数值字段，每个字段提供 2 到 100 个有限候选，
笛卡尔积最多 100000 个组合；`algorithms` 不能重复。日期为 `YYYY-MM-DD`，周期使用 `D/W/M/Y`，
随机种子为非负 32 位整数。Runtime 只查询一次覆盖最早训练窗口至最后持有窗口的完整数据；每个算法
生成一个同名 Parquet。先轮询报告的 `workflow_workspace_id`，仅 `SUCCESS` 后读取输出。

## 输出

当前工作流成功后：

```text
list_workflow_outputs("backtest", workflow_instance_id)
```

| 名称 | 文件 | 内容 |
| --- | --- | --- |
| `trade_details` | `trade_details.parquet` | 订单状态事件；同一订单多行，当前没有费用列 |
| `daily_positions` | `daily_positions.parquet` | 每日盘后证券持仓；当前卖出量/额字段有已知限制 |
| `daily_portfolios` | `daily_portfolios.parquet` | 每日现金、市值、权益、净值、累计收益和累计费用 |
| `daily_trading_statistics` | `daily_trading_statistics.parquet` | 可选；仅在运行节点插件支持对应接口时生成 |

下载与鉴权见 `arena://docs/overview/workflows`。读取报告前必须执行
`arena://docs/backtest/results` 的字段解释、已知限制、对账公式和 QA 清单。

## 完整调用顺序

```text
1. 读取 backtest/request、backtest/dolphindb、backtest/results、overview/dsl、schemas/backtest，及与
   当前任务对应的 dynamic-pool、optimization、callback-data 或 qa 契约
2. 发现 DSL 算符和需要的 DolphinDB 内置函数签名
3. create_project("backtest", title)
4. run_backtest(project_id, complete_parameters)
5. 按 Workspace 轮询；失败时读 Attempt、Task 和完整日志
6. SUCCESS 后列出四个输出并执行订单/成交/持仓/现金/费用 QA
7. 需要固化时 save_version("backtest", ...)
8. 需要网格研究时从已保存版本创建 research，轮询唯一 Workspace，成功后下载 `results.parquet`
```

MCP 不提供 Backtest 项目、版本、研究、Workspace、Attempt 或输出删除功能。
