# Backtest 请求

Backtest 使用 Factor Query 准备候选代码和回调历史数据，将日线转换为开盘、收盘单档合成快照，并在
DolphinDB Backtest 插件中执行八个生命周期回调。本页定义请求 JSON；回调可用数据、订单、持仓、
事件接口、执行时序和价格尺度见 `arena://docs/backtest/dolphindb`；可调用的插件函数白名单见
`arena://docs/backtest/interfaces`。
四张结果表的字段、费用和对账规则见 `arena://docs/backtest/results`。

专项契约按用途拆分：动态数据域见 `arena://docs/backtest/dynamic-pool`，二次规划与目标权重见
`arena://docs/backtest/optimization`，回调对象见 `arena://docs/backtest/callback-data`，运行、核验和
保存顺序见 `arena://docs/backtest/qa`，插件函数能力见 `arena://docs/backtest/interfaces`。这些文档只
定义接口与边界，不提供具体策略或构造。

DolphinDB 插件原始定义可直接查阅
[股票回测配置](https://docs.dolphindb.com/zh/plugins/backtest/stock.html)、
[Backtest 接口说明](https://docs.dolphindb.com/zh/plugins/backtest/interface_description.html) 和
[模拟撮合引擎使用教程](https://docs.dolphindb.com/zh/tutorials/matching_engine_simulator.html)。Arena
文档负责说明当前 Runtime 实际固定了哪些配置，不能用它替代官方完整插件文档。

## 调用

```text
create_project(application="backtest", title=...)
run_backtest(project_id=result.id, parameters=<完整 Backtest 请求>)
get_workspace_status(workspace_id) -> SUCCESS
list_workflow_outputs(application="backtest", workflow_instance_id=...)
save_version(application="backtest", project_id=..., workflow_instance_id=..., remark=...)
```

`run_backtest` 会先连接 DolphinDB 编译 `utils` 和 callbacks。编译失败时不会创建 Workspace；编译
成功后返回的 ID 只表示工作流已提交，仍需轮询。

未保存版本再次运行、保存后创建下一版本、历史 Attempt 参数和结果保留规则见
`arena://docs/backtest/api`；通用对象关系见 `arena://docs/overview/projects`。

## 顶层字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `config` | object | 否 | 见下文 | 插件资金、费用和可开放选项 |
| `params` | object | 否 | `{}` | 用户回调参数，通过 `getParams()` 或 `getParam(key)` 读取 |
| `codes_query` | FactorQuery 或 null | 否 | `null` | 第一阶段候选代码查询，支持双源码 |
| `dataset_query` | FactorQuery | 是 | — | 第二阶段行情和策略数据查询，支持双源码 |
| `adj` | `hfq`、`qfq` 或 null | 否 | `null` | 合成快照价格复权方式 |
| `annual_trading_days` | integer | 否 | `250` | 年化指标使用的交易日数，至少 1 |
| `risk_free_rate` | finite number | 否 | `0.04` | Sharpe 年化无风险收益率 |
| `utils` | string | 否 | `""` | 回调注册前原样执行的 DolphinDB 脚本 |
| `callbacks` | object | 是 | — | 必须且只能包含八个固定回调 |

模型为 strict 且禁止额外顶层字段。不要把数字写成字符串。

`codes_query` 与 `dataset_query` 均可通过 `dsl_source` 同时保存 JSON/Python 源码，`language` 决定
本次编译版本。活动源码、未选中草稿和 Python 必需结果变量的规则见
`arena://docs/overview/dsl` 的“JSON 与 Python 双源码”。

`adj` **不会复权 `dataset_query` 的 factors/derivatives**。它只改变用于插件撮合的 message 及由其
产生的委托、成交和持仓价格。凡是把 DSL 中任意原始价格、价格差或价格型派生值与 message/持仓
价格比较或相除，都必须先按运行契约换算到同一价格尺度。

`run_backtest` 的 `parameters` 必须直接传完整 Backtest 请求对象。精确机器可读结构以
`arena://schemas/backtest` 为准；本页不提供可能被误当成业务实现的完整请求。

## 代码范围与两阶段查询

`codes_query=null` 时：

- `dataset_query.codes` 必须非空；
- 当前股票回测只接受 `.SH` 和 `.SZ` 代码。

`codes_query` 非空时：

1. 执行第一阶段；
2. 对过滤后结果的 `code` 取整个区间的去重并集；
3. 用该并集覆盖 `dataset_query.codes`；
4. 执行第二阶段完整查询。

第一阶段不是每日 join。若某个有效状态需要逐日生效，应在第二阶段重新输出 BOOL derivative。需要
保留失效代码以继续观察或退出时，不能用第二阶段 `filters` 删除这些行；完整时点契约见
`arena://docs/backtest/dynamic-pool`。

Backend 在生成调度器输入 JSON 时统一补全
`dataset_query.derivatives.stock_pool_member`：动态股票池复用 `codes_query` 中参与过滤的同名节点，
静态股票池使用恒真节点。该节点只作为策略可读取的逐日状态列，不会自动加入第二阶段 `filters`；
保存的编辑器源码保持不变。Runtime 只接收补全后的请求，不再负责猜测或注入该业务字段。

## `dataset_query`

Runtime 自动补充合成快照需要的基础因子：

```text
open, low, high, close, up_limit, down_limit, pre_close
```

`adj` 非 null 时还会读取 `adj_factor`。调用方不需要把这些列重复写入 `factors`，但
`FactorQuery` 本身仍要求 `factors` 或 `derivatives` 至少一项非空。

保留规则：

- `symbol`、`tradeTime` 由回测框架生成，不能作为 factor 或 derivative；
- `adj` 非 null 时不能定义名为 `adj_factor` 的 derivative；
- derivatives 存在于策略历史数据表，不会自动出现在快照 message；
- 策略时序特征必须由 `lookback` 提供足够历史；
- 未来收益、负 shift 等标签不能作为回测信号。

`dataset_query` 也必须遵守最小列原则。只有回调需要通过 `getLastData` / `getHistoryData` 按名称读取、
需要作为最终过滤器、需要直接核验，或被多个高成本下游节点复用的 derivative 才保留顶层名称。
一次性中间算术、比较、转换和掩码在算符 definition 允许时应嵌套到最终信号节点中。所有顶层
derivative 都会扩宽回测会话中的历史数据表；它们不会进入 snapshot message，却仍会占用 DolphinDB
内存，因此不能为“分步骤展示”保留无用中间列。具体取舍见 `arena://docs/overview/dsl` 的“嵌套优先
与结果列预算”。

动态代码域只遵循“第一阶段候选并集、第二阶段逐日状态、回调读取严格历史截面”这一通用契约。
具体 DSL 字段和算符必须通过 Catalog、`describe_dsl_operator` 和目标数据定义自行确认。

## `config`

默认配置：

```json
{
  "cash": 1000000,
  "commission": 0,
  "tax": 0,
  "enableMinimumPerTransactionFee": true
}
```

Runtime 允许并校验的常用字段：

| 字段 | 类型与约束 | 说明 |
| --- | --- | --- |
| `cash` | finite number > 0 | 初始资金 |
| `commission` | finite number >= 0 | 手续费率 |
| `tax` | finite number >= 0 | 印花税率 |
| `syntheticSpread` | finite number，`0 <= x < 1` | 合成盘口完整相对买卖价差 |
| `benchmark` | `INDEX_CODES` 中以 `.SH` 或 `.SZ` 结尾的指数代码 | 可选业绩基准，例如 `000300.SH` |
| `latency` | integer >= 0 | 插件订单延时参数 |
| `enableMinimumPerTransactionFee` | boolean | 最低单笔费用 |
| `enableSellCloseRestrict` | boolean | 卖出可用量限制 |
| `outputOrderInfo` | boolean | 请求插件输出可选 `orderInfo`；不保证存在或形成结构化拒单原因 |
| `outputSeqNum` | boolean | 请求可选订单状态事件序号；只有实际 `trade_details` Schema 含 `seqNum` 时才能用于稳定事件排序 |
| `outputTradeSeqNum` | boolean | 请求可选成交序号；只有实际 Schema 含 `tradeSeqNum` 时才能用于成交排序 |
| `outputQueuePosition` | 0、1 或 2 | Runtime 可校验该插件选项，但非零值只适用于含逐笔行情模式；当前固定 `dataType=1` 不应设置 |

其余 Runtime 已声明的插件 boolean 选项也按 boolean 校验。`config` 是开放字典，能通过 JSON 校验
不代表某个 DolphinDB 版本或当前快照模式一定支持该选项。两个序号开关也只表示向插件请求对应
可选列；未设置或实际 Schema 未返回列时，`trade_details` 仍没有稳定的单行事件键。

`benchmark` 使用 Tushare 沪深代码格式，并且只能选择 Runtime `INDEX_CODES` 中已经配置、由
`index-daily` Worker 写入 `coreTable` 的指数。Runtime 会单独读取相同回测区间的指数日行情，转换为
Backtest 插件使用的 `XSHG/XSHE` 代码并加入行情回放；当前不接受其他交易所后缀，用户也不应把该
指数重复加入策略代码域。

```json
{
  "config": {
    "cash": 1000000,
    "commission": 0.0003,
    "tax": 0.001,
    "benchmark": "000300.SH"
  }
}
```

省略 `benchmark` 表示不计算基准。启用后，插件会在 `daily_portfolios` 中增加
`benchmarkClosePrice` 和 `benchmarkNetValue`，并在直接读取插件收益汇总时提供基准收益、超额收益、
Alpha 和 Beta。基准使用指数未复权价格，不跟随股票策略的 `adj` 设置。

以下字段由 Runtime 强制设置，用户传入会被拒绝：

```text
startDate, endDate, strategyGroup, dataType, msgAsTable, matchingMode,
frequency, callbackForSnapshot, msgAsPiecesOnSnapshot,
matchingRatio, orderBookMatchingRatio
```

实际固定值见 `arena://docs/backtest/dolphindb`。

## `params`

`params` 只上传给用户回调，不传给插件。`getParams()` 返回完整字典；`getParam(key)` 返回指定值，
key 为空或不存在时直接报错，不提供隐式默认值。在 `initialize` 中读取参数后，应进行 `long()`、
`double()`、`bool()` 等明确类型转换。

## `utils` 与 callbacks

`utils` 是一个 DolphinDB 脚本字符串，在 callbacks 之前原样执行。它可以包含多个函数和确有需要
的顶层语句，不是函数名到源码的字典。callbacks 引用的每个自定义函数必须在同一请求的 `utils`
中定义。

Runtime 会在编译预检和正式执行时先加载 `factor`、再加载 `backtest` 模块。因此 `utils` 和八个
callbacks 可以直接调用因子分析使用的 `factor::factorPreprocess`，不需要复制去极值、标准化、
中性化或分组实现。`getIndustry()` 提供与 Factor 研究同源、代码已转换为 `.XSHG/.XSHE` 的行业
字典；Runtime 不会自动处理 `dataset_query` 或注入行业列。完整签名、输入要求、时点边界及与
DSL `controls.neutralize_by` 的区别见
`arena://docs/backtest/dolphindb` 的“因子分析预处理模块”。

JSON 必须正好包含以下八个 key，且每个值必须是同名完整函数定义：

```dos
def initialize(mutable context)
def beforeTrading(mutable context)
def onBar(mutable context, message, indicator)
def onSnapshot(mutable context, message, indicator)
def onOrder(mutable context, orders)
def onTrade(mutable context, trades)
def afterTrading(mutable context)
def finalize(mutable context)
```

未使用的回调仍要定义并可 `return NULL`。不能改变函数名、参数数量或只传函数体。当前固定模式在
09:30 和 15:00 触发 `onSnapshot`，不触发 `onBar`；准确生命周期见运行契约。

回调必须从真实持仓和挂单状态出发，并遵守部分成交与复权尺度等运行契约；结果 QA 见
`arena://docs/backtest/results`。本页不提供会被误当成生产代码的简化模板。

## 输出

普通项目固定请求四个 Parquet：

| 逻辑名 | 文件名 | 内容 |
| --- | --- | --- |
| `trade_details` | `trade_details.parquet` | 订单状态事件；同一订单可有多行 |
| `daily_positions` | `daily_positions.parquet` | 每日盘后证券持仓 |
| `daily_portfolios` | `daily_portfolios.parquet` | 每日现金、权益、净值、费用和盈亏 |
| `daily_trading_statistics` | `daily_trading_statistics.parquet` | 每日实际成交量、成交额和方向均价 |

四个文件均为必需输出；任何结果接口调用失败都会使任务失败。Workspace SUCCESS 只说明程序执行
完成，不证明信号无未来、价格尺度一致或成交逻辑可信。统一 QA
清单、字段字典、费用和指标口径见 `arena://docs/backtest/results`。

## 批量执行与研究报告

- `run_backtest_batch` 对应网页执行队列，每个成功项自动保存成版本；
- `create_backtest_fee_analysis` 从已保存版本生成手续费率网格；
- `create_backtest_research(analysis_type="sensitivity", ...)` 提交参数敏感性网格；
- 每条研究只创建一个 `sensitivity` Workspace，所有 case 复用同一份完整区间数据和消息表；
- `get_backtest_research` 读取该研究和唯一 Workspace，运行过程继续使用通用工作流、Task、日志接口；
- 工作流成功时 Runtime 已写出一份 `results.parquet`，其中每行对应一个 case，前端直接用 DuckDB
  生成报告，不再由后端逐项下载普通回测结果或二次计算指标。

敏感性研究的 `parameter_sets` 必须是完整 Backtest 请求对象数组。先用 `get_version` 读取
基准参数，对每个网格点深拷贝并修改相应用户参数，再提交；不能只发送
`{"params": {"KEY": value}}` 这样的局部对象。精确工具字段见 `arena://docs/backtest/api`。

## 提交前检查

- 静态代码域非空，或第一阶段能产生候选代码；
- 第二阶段仍包含退出持仓所需的代码；
- `dataset_query` 已嵌套单次使用的中间节点，只保留回调、过滤、复用或核验真正需要的顶层列；
- 所有信号通过 `getLastData` / `getHistoryData` 使用当前日期之前的数据；
- 调用 `factor::factorPreprocess` 时只传严格历史表，并显式提供与研究口径一致的市值列和行业列；
- `params` 的 key 均存在并在 initialize 转换类型；
- `utils` 包含 callbacks 引用的所有函数；
- callbacks 恰好八个，名称和参数数量正确；
- 目标仓位合计、费用和 spread 不会系统性造成资金不足；
- 不把 `submitOrder` 返回订单号当作成交结果；
- 不访问 Runtime 会话内部变量或把 DSL derivative 当成 message 列；
- 价格型 DSL 指标与 message/持仓价格已转换到同一尺度；
- 已设计挂单、部分成交、撤单拒绝和期末未成交的处理；
- 已按 `arena://docs/backtest/results` 对四个输出执行回测后 QA；
- 动态数据域、优化数值和回调对象均按对应契约检查，不根据函数名猜测结构；
- 已理解当前只有 Schema 与编译预检，没有独立工具自动验证业务时点、矩阵可行性或运行时目标值。
