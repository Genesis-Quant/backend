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
list_projects("backtest", page=1, page_size=20, search=null, sort_by="updated_at", sort_order="desc")
create_project("backtest", title, parameters=<完整 Backtest 请求>)
get_project("backtest", project_id)
update_project("backtest", project_id, title)
delete_project("backtest", project_id)
```

Backtest 项目必须在创建时提交完整初始参数；创建接口和执行接口使用同一严格双源码契约，不会先
保存空参数再由详情页补写。

`search` 按项目名称或 ID 片段过滤，`sort_order` 为 `asc` 或 `desc`。`sort_by` 可选 `id`、`title`、
`latest_version`、`totalReturn`、`annualReturn`、`sharpeRatio`、`annualVolatility`、`maxDrawdown`、
`dailyWinningRate`、`updated_at`。

`update_project` 只改标题。

## 执行一次回测

```text
run_backtest(project_id, parameters)
```

`parameters` 必须直接是完整 Backtest 请求对象，不能只传修改部分。`codes_query` 和 `dataset_query`
均支持 JSON/Python 双源码，活动版本由各自的 `dsl_source.language` 决定。请求至少应包含合法
`dataset_query` 和八个固定名称 callbacks；完整外形见 request 文档。

调用分两步：

1. Backend 编译活动 DSL 源码，用 Runtime 模型严格校验生成参数，并在 DolphinDB 中编译 `utils` 与
   八个回调；
2. 编译成功后才创建 Attempt 并提交调度器。

编译错误会让 MCP 工具直接 `isError=true`，此时没有 Workspace。提交成功返回 `workspace_id` 与可能
暂为空的 `workflow_instance_id`；之后按 Workspace 轮询。

## 版本读取与保存

```text
list_versions("backtest", project_id)
get_version("backtest", project_id, version)
save_version("backtest", project_id, workflow_instance_id, remark="")
update_version("backtest", project_id, version, remark)
delete_version("backtest", project_id, version)
```

- 列表包含已保存版本和当前未保存版本。
- 版本详情包含完整 parameters、Workspace/Instance、summary、保存状态和备注。
- 只能保存当前未保存版本绑定的当前成功 Instance；保存后自动创建下一未保存版本。
- `update_version` 只修改显示备注。
- 项目与版本删除分别要求个人主页中的 Backtest 项目、Backtest 版本权限。当前未保存版本不能单独
  删除；删除已保存版本会一并删除其手续费分析、参数敏感性分析和参数调优报告，并永久留下版本号
  空缺。任一关联工作流仍在活动时都会拒绝删除。

## 普通批量执行

```text
run_backtest_batch(project_id, items)
```

`items` 为 1 到 100 项。每项必须包含唯一 `client_id`、可选 `remark` 和完整 Backtest 请求对象，
不是局部 override；其中的 Query 同样支持双源码：

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

`version` 必须是已保存版本；`parameter_sets` 为 1 到 100 份完整 Backtest 请求对象。服务端不会把
局部字典合并进基准参数，因此调用方必须先读取基准 `get_version(...).parameters`，在本地生成每个
完整请求，再提交。参数敏感性分析只允许各请求的 `params` 不同，手续费分析只允许
`config.commission` 不同；其余数据查询、代码和回测配置必须与来源版本一致。校验通过后只创建一个
研究 Workspace。

### 创建手续费分析

```text
create_backtest_fee_analysis(project_id, version, rates)
```

`rates` 为 1 到 100 个 `[0, 1]` 内且互不重复的费率。重复值会直接校验失败；服务端保留提交顺序，
并按该顺序从基准版本生成完整请求，不会自动去重或排序。

### 读取研究结果

```text
get_backtest_research(research_id)
list_backtest_research_outputs(research_id)
delete_backtest_fee_analysis(research_id)
delete_backtest_sensitivity_analysis(research_id)
```

轮询返回的唯一 `workflow_workspace_id`；运行中可通过通用 Workspace、Attempt、Task 和日志工具查看
进度。工作流成功后，Runtime 已经生成 `results.parquet`，不再需要调用后端二次计算接口。网页通过
研究输出接口下载该文件，并用 DuckDB 读取每个 case 的参数、状态、错误和指标。单个 case 失败会以
`status=FAILURE` 行保留；至少一个 case 成功时工作流仍可成功，因此必须检查每一行的 `status`。
`list_backtest_research_outputs` 在成功后返回固定的 `results` 输出及认证下载路径。

`results.parquet` 每个请求 case 恰好一行，列契约如下：

| 列 | 类型 | 含义 |
| --- | --- | --- |
| `case_index` | LONG | 从 1 开始，对应 `parameter_sets` 或 `rates` 的原始提交顺序 |
| `analysis_type` | STRING | `fee_analysis` 或 `sensitivity` |
| `params` | STRING | 该 case 实际使用的策略 `params` JSON，不是完整 Backtest 请求 |
| `commission` | DOUBLE | 该 case 实际使用的手续费率 |
| `status` | STRING | `SUCCESS` 或 `FAILURE` |
| `error` | STRING | 失败原因；成功行为空字符串 |
| `total_return` | DOUBLE | 区间累计收益，小数口径 |
| `cagr` | DOUBLE | 年化收益，小数口径 |
| `sharpe` | DOUBLE | 按来源版本年化交易日数和无风险利率计算的 Sharpe |
| `sortino` | DOUBLE | 按同一日收益和年化交易日数计算的 Sortino |
| `volatility` | DOUBLE | 年化波动率，小数口径 |
| `max_drawdown` | DOUBLE | 最大回撤，成功行使用小于等于 0 的有符号小数 |
| `win_rate` | DOUBLE | 日收益胜率，小数口径 |
| `calmar` | DOUBLE | 标准化收益摘要的 `drawdownRatio`；当前 Runtime 按 `annualReturn / summary.maxDrawdown` 写入，未额外取绝对值 |
| `total_fee` | DOUBLE | 区间费用增量合计，货币金额 |

失败行保留 `case_index`、类型、参数、手续费、状态和错误，其余指标为 NULL。读取端必须用
`case_index` 关联原始输入，不能依赖 Parquet 物理行顺序，也不能把 `params` 当作完整请求反序列化。

两类研究共用 ID 空间，但删除工具按类型分开并分别受个人主页权限控制。传入另一种研究的 ID 会
直接拒绝，不会把一个总开关同时授权两种分析。活动状态研究仍不能删除。

Backend 会在工作流成功后校验 `results.parquet` 的行数、`case_index` 完整性和逐行 `status`，再把
`completed_count`、`failed_count` 与当前 workflow instance 绑定。校验完成前研究状态为
`RESULT_PENDING`；读取或结构校验失败时为 `RESULT_FAILED` 并在 `error` 返回原因。重跑 Workspace
产生新 Attempt 后，旧实例的计数不会用于新实例。即使全部 case 都失败，Runtime 仍保存逐行错误，
工作流可以成功且 `completed_count=0`，因此业务有效性必须以这两个计数和结果行状态为准。

源码审计存在明确边界：来源版本本身的双源码可从 `get_version(...).parameters` 读取；研究详情和
研究 Worker Attempt 不逐字节回显由来源版本派生出的完整双源码请求，Attempt 保存的是已编译并移除
`dsl_source` 的 Runtime JSON。因此当前接口只能核对来源版本和实际执行语义，不能据此声称已经完成
派生研究记录的源码级审计。需要这种审计时，应在创建研究前归档来源版本 parameters 及其内容哈希。

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
delete_backtest_optimization(optimization_id)
```

`parameter_space` 只能引用来源版本 `params` 中已有的数值字段，每个字段提供 2 到 100 个有限候选，
笛卡尔积最多 100000 个组合；`algorithms` 不能重复。日期为 `YYYY-MM-DD`，周期使用 `D/W/M/Y`，
随机种子为非负 32 位整数。Runtime 只查询一次覆盖最早训练窗口至最后持有窗口的完整数据；每个算法
生成一个同名 Parquet。先轮询报告的 `workflow_workspace_id`，仅 `SUCCESS` 后读取输出。
删除报告要求个人主页启用参数调优删除权限，活动状态报告不能删除。

这里的“参数调优”是有限候选网格上的滚动样本内选择，与
`arena://docs/backtest/optimization` 中策略自行调用 OSQP 计算组合权重不是同一功能。所有方法都从
一个随机候选开始，在最多 `min(evaluation_budget, 参数组合总数)` 个互不重复候选内搜索，以训练窗口
Backtest `sharpeRatio` 最大的已评价候选作为该窗口最终参数，再在紧随其后的持有窗口运行。参数名和
各自候选值会先排序，以确保同一输入和 seed 可复现；同一窗口、同一参数组合的训练分数会在不同算法
和重复之间复用，但每个算法、重复和窗口使用独立派生 seed。

当前 `algorithms` 精确枚举及其有限网格提议规则：

| 类别 | 枚举 | 行为 |
| --- | --- | --- |
| 随机与空间填充 | `random_search`、`latin_hypercube`、`halton`、`maximin` | 随机抽样、分层/低差异位置或距已访问点最远位置 |
| 局部搜索 | `hill_climb`、`coordinate_descent`、`pattern_search`、`tabu_search` | 围绕当前最好点、单坐标、固定方向或短期禁忌邻域提议 |
| 接受准则搜索 | `simulated_annealing`、`threshold_accepting`、`great_deluge` | 按温度、阈值或水位决定轨迹当前点 |
| 群体与进化启发式 | `differential_evolution`、`particle_swarm`、`genetic_algorithm`、`evolution_strategy`、`cross_entropy` | 根据随机群体、速度、交叉或精英分布提议 |
| 代理与自适应 | `tpe`、`rbf_surrogate`、`knn_ucb`、`adaptive_random` | 根据已评价点的好坏分区、距离加权预测、不确定性或探索/利用概率提议 |

这些名称表示 Runtime 对离散候选点实现的提议规则，不是对应第三方连续优化库的完整实现；每个连续
提议最终都会吸附到最近的未评价候选组合。比较算法时必须保持参数空间、日期、预算、重复次数和 seed
一致。

`start_date` 是第一段样本外持有区间起点；每个训练窗口位于对应持有窗口之前，持有窗口按
`holding_period` 连续、不重叠地平移，最后一段截断到 `end_date`。每次 repetition 的拼接净值从 1
重新开始。每个输出逻辑名就是算法枚举，文件名为 `<algorithm>.parquet`，每行是一日样本外组合状态：

| 列 | 含义 |
| --- | --- |
| `time` | 原 `daily_portfolios.tradeDate` |
| `window_net_value` | 当前持有窗口内部从引擎取得的净值 |
| `path_net_value` | 同一算法、同一 repetition 跨持有窗口连续拼接后的净值 |
| `daily_return` | 从 `path_net_value` 计算的相邻日收益，窗口首日以前一窗口终值衔接 |
| `algorithm`、`repetition`、`window` | 算法、重复编号和窗口编号 |
| `training_start`、`training_end` | 本行对应参数的样本内区间 |
| `holding_start`、`holding_end` | 本行所在样本外持有区间 |
| `training_sharpe` | 被选参数在训练窗口的 Sharpe |
| `evaluation_count` | 本窗口该算法访问的候选数；不等于本次新增执行的训练回测数，因为分数可复用 |
| `initial_params`、`selected_params` | 随机初始组合与最终组合的 JSON 字符串 |
| 其余列 | 除 `tradeDate`、`netValue` 被重命名外，插件 `daily_portfolios` 的原有列 |

当前输出不保存每个已评价候选及其分数，只保存初始组合、最终组合、最终训练 Sharpe 和评价数量；不能
从 Parquet 反推出完整搜索轨迹。

参数调优沿用相同的源码审计边界：`get_version(...).parameters` 可以读取来源版本双源码，但
`get_backtest_optimization` 和优化 Worker Attempt 不回显派生执行请求中的两份源码。若需要逐字节
证明来源，创建调优报告前必须自行归档来源版本 parameters 及哈希；不能用已编译 Runtime JSON 反推
原始 JSON/Python 源码文本。

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
| `daily_trading_statistics` | `daily_trading_statistics.parquet` | 每日实际成交量、成交额和方向均价 |

以上均为必需输出；任何结果接口调用失败都会使工作流失败。下载与鉴权见
`arena://docs/overview/workflows`。读取报告前必须执行
`arena://docs/backtest/results` 的字段解释、已知限制、对账公式和 QA 清单。

## 完整调用顺序

```text
1. 读取 backtest/request、backtest/dolphindb、backtest/results、overview/dsl、schemas/backtest，及与
   当前任务对应的 dynamic-pool、optimization、callback-data 或 qa 契约
2. 发现 DSL 算符和需要的 DolphinDB 内置函数签名
3. create_project("backtest", title, parameters=complete_parameters)
4. run_backtest(project_id, complete_parameters)
5. 按 Workspace 轮询；失败时读 Attempt、Task 和完整日志
6. SUCCESS 后列出四个输出并执行订单/成交/持仓/现金/费用 QA
7. 需要固化时 save_version("backtest", ...)
8. 需要网格研究时从已保存版本创建 research，轮询唯一 Workspace，成功后下载 `results.parquet`
```

MCP 不提供 Backtest Workspace、Attempt、工作流实例或输出的独立删除功能。
