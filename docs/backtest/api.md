# Backtest API

本文件说明策略回测的项目、编译提交、版本、批量执行、专项研究和输出 API。请求对象见
`arena://docs/backtest/request`，回调、行情、价格尺度、撮合、订单簿、事件和 QA 见
`arena://docs/backtest/dolphindb`，Schema 见 `arena://schemas/backtest`。通用工作流诊断见
`arena://docs/overview/workflows`。

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

每个成功项自动保存为独立递增版本。分别轮询返回的 Workspace。`client_id` 用于幂等识别；重试同一
批次复用原值。服务端会先校验全部参数并逐项完成 DolphinDB 脚本编译，全部通过后才创建 Workspace；
任一项编译失败时整批不提交。工具调用即提交，不存在 MCP 侧本地队列。

## 手续费与参数敏感性研究

专项研究基于一个已保存 Backtest 版本，属于该项目/版本，但每个研究项有独立 Workspace。

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
完整请求，再提交。全部参数和 DolphinDB 脚本通过预检后才创建研究及 Workspace。

### 创建手续费分析

```text
create_backtest_fee_analysis(project_id, version, rates)
```

`rates` 为 1 到 100 个 `[0, 1]` 内费率，服务端去重排序，并从基准版本生成完整请求。

### 读取和计算研究结果

```text
get_backtest_research(research_id)
calculate_backtest_research(research_id)
```

先轮询 `get_backtest_research` 中所有 Workspace。调度完成但指标尚未生成时，研究状态为
`RESULT_PENDING`；此时调用 `calculate_backtest_research` 收集成功 Parquet 并计算指标。单项
`result_error` 会进入失败计数和总错误，不能把工作流 `SUCCESS` 等同于研究结果可用。

## 输出

当前工作流成功后：

```text
list_workflow_outputs("backtest", workflow_instance_id)
```

| 名称 | 文件 | 内容 |
| --- | --- | --- |
| `trade_details` | `trade_details.parquet` | 成交明细、价格、数量、方向和费用 |
| `daily_positions` | `daily_positions.parquet` | 每日证券持仓明细 |
| `daily_portfolios` | `daily_portfolios.parquet` | 每日现金、资产与组合净值 |
| `daily_trading_statistics` | `daily_trading_statistics.parquet` | 每日委托与成交统计 |

下载与鉴权见 `arena://docs/overview/workflows`。读取报告前必须执行
`arena://docs/backtest/dolphindb` 的 QA 清单。

## 完整调用顺序

```text
1. 读取 backtest/request、backtest/dolphindb、overview/dsl、schemas/backtest
2. 发现 DSL 算符和需要的 DolphinDB 内置函数签名
3. create_project("backtest", title)
4. run_backtest(project_id, complete_parameters)
5. 按 Workspace 轮询；失败时读 Attempt、Task 和完整日志
6. SUCCESS 后列出四个输出并执行订单/成交/持仓/现金/费用 QA
7. 需要固化时 save_version("backtest", ...)
8. 需要网格研究时从已保存版本创建 research，并在 RESULT_PENDING 计算结果
```

MCP 不提供 Backtest 项目、版本、研究、Workspace、Attempt 或输出删除功能。
