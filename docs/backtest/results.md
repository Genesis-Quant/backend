# Backtest 结果与审计契约

本页定义标准 Parquet 的行粒度、关联字段、订单状态和账务口径。结果表及附加列可能随插件版本及
`outputOrderInfo`、`outputSeqNum`、`outputTradeSeqNum` 配置变化，读取时必须先检查实际输出列表和
Parquet Schema。四张标准结果表都是必需输出；任一接口调用失败或文件缺失都会使工作流失败，不做
低版本插件降级。

## 行粒度与关联

| 输出 | 行粒度 | 候选键或关联字段 | 主要用途 |
| --- | --- | --- | --- |
| `trade_details` | 一次订单状态事件 | `orderId` 关联同一订单；默认没有单行稳定主键 | 订单生命周期、拒单和未成交审计 |
| `daily_positions` | 证券 × 交易日的盘后持仓记录 | `(symbol, tradeDate)` | 持仓数量和估值 |
| `daily_portfolios` | 组合 × 交易日 | `tradeDate` | 现金、权益、净值、盈亏和费用 |
| `daily_trading_statistics` | 有交易统计的证券 × 交易日 | `(symbol, tradeDate)` | 实际成交量、成交额和均价 |

四表没有统一的成交主键。使用 `orderId` 还原订单事件；使用 `symbol` 和 `tradeDate` 关联持仓与日成交；
再按 `tradeDate` 汇总到组合表。`NULL`、数值 0 和缺行含义不同，聚合时只能按明确口径补值。时间戳
当前没有时区元数据，按中国证券交易所本地时间解释。

四个 Workspace 输出与插件接口一一对应：

| 输出 | Runtime 取数接口 |
| --- | --- |
| `trade_details` | `Backtest::getTradeDetails(engine)` |
| `daily_positions` | `Backtest::getDailyPosition(engine)` |
| `daily_portfolios` | `Backtest::getDailyTotalPortfolios(engine)` |
| `daily_trading_statistics` | `Backtest::getDailyTradingStatistics(engine)` |

`return_summary` 和 `engine_stat` 不属于标准业务输出。前者与 Backend/浏览器从 `daily_portfolios` 计算
的指标重复，后者属于引擎诊断；MCP 不把它们列为可下载的 Backtest Workspace 结果。

## `trade_details`

该表是订单状态事件表，不是“一笔订单一行”，也不是纯成交明细。同一 `orderId` 可同时存在创建、
更新和终态事件。

字段字典：

| 字段 | 类型 | 必需 | 语义 |
| --- | --- | --- | --- |
| `orderId` | LONG | 是 | 订单 ID；聚合同一订单的全部事件 |
| `symbol` | STRING | 是 | `.XSHG/.XSHE` 证券代码 |
| `direction` | INT | 是 | 真实订单方向：1 买开、2 卖开、3 卖平、4 买平 |
| `sendTime` | TIMESTAMP | 是 | 委托时间 |
| `orderPrice` | DOUBLE | 是 | 委托价格 |
| `orderQty` | LONG | 是 | 委托数量 |
| `tradeTime` | TIMESTAMP | 是 | 当前状态事件的成交相关时间；非成交状态不能据此认定成交 |
| `tradePrice` | DOUBLE | 是 | 当前状态事件的成交相关价格 |
| `tradeQty` | LONG | 是 | 当前状态事件的累计成交量，不能跨事件行直接求和 |
| `orderStatus` | INT | 是 | 状态码，见下表 |
| `label` | STRING | 是 | 调用方业务标签；不能代替 `direction` |
| `orderInfo` 或 `outputOrderInfo` | STRING | 否 | `outputOrderInfo=true` 且插件实际输出时的风控文本；列名以真实 Schema 为准 |
| `seqNum` | LONG | 否 | `outputSeqNum=true` 时的稳定事件序号 |
| `tradeSeqNum` | LONG | 否 | `outputTradeSeqNum=true` 且插件实际输出时的成交序号 |
| `strategyName` | STRING | 否 | 模拟交易模式可能出现；Arena 当前历史回测不依赖 |

状态语义：

| 状态 | 含义 | 终态 | 成交处理 |
| ---: | --- | --- | --- |
| `4` | 已报 | 否 | 不是成交 |
| `0` | 部分成交 | 否 | `tradeQty` 为累计成交量 |
| `1` | 全部成交 | 是 | 已完成成交 |
| `2` | 撤单成功 | 是 | 该事件的 `tradeQty` 是撤单成功量，不是新增成交量 |
| `-1` | 审批或风控拒绝 | 是 | 不是成交 |
| `-2` | 撤单拒绝 | 否 | 原订单可能仍活动，继续读取后续事件或挂单 |
| `-3` | 回测结束仍未成交 | 是 | 不是成交，也不等于主动撤单 |

处理规则：

- 按 `orderId` 聚合，事件行数与唯一订单数分别报告；
- 终态集合为 `{1, 2, -1, -3}`，不能按状态码大小判断；
- 不对同一订单各事件行的 `tradeQty` 求和；
- 使用 `daily_trading_statistics` 核对实际成交量、成交额和方向均价，再按 `orderId` 结合
  `trade_details` 的累计成交字段审计订单生命周期；
- 默认没有序号列时，Parquet 物理行顺序不是稳定业务顺序；
- 当前 MCP 不合成目标金额、可用现金和具体拒绝规则。拒单详情仅在实际 `orderInfo` 或日志可用时辅助
  解释，不能假定每笔拒单都有完整原因。

`trade_details` 当前没有费用列。费用从 `onTrade.totalFee` 或 `daily_portfolios.totalFee` 的日增量读取。

## `daily_positions`

字段字典：

| 字段 | 类型 | 必需 | 语义 |
| --- | --- | --- | --- |
| `symbol` | STRING | 是 | 证券代码 |
| `tradeDate` | DATE | 是 | 盘后交易日期 |
| `lastDayLongPosition` | LONG | 是 | 昨日多头持仓数量 |
| `lastDayShortPosition` | LONG | 是 | 昨日空头持仓数量 |
| `longPosition` | LONG | 是 | 当日盘后多头持仓数量 |
| `longPositionAvgPrice` | DOUBLE | 是 | 多头成交均价，不是当日估值价 |
| `shortPosition` | LONG | 是 | 当日盘后空头持仓数量 |
| `shortPositionAvgPrice` | DOUBLE | 是 | 空头成交均价 |
| `todayBuyVolume` | LONG | 是 | 当日买入成交数量 |
| `todayBuyValue` | DOUBLE | 是 | 当日买入成交金额 |
| `todaySellVolume` | LONG | 是 | 插件报告的当日卖出数量；有当前部署限制，见下文 |
| `todaySellValue` | DOUBLE | 是 | 插件报告的当日卖出金额；有当前部署限制，见下文 |
| `closePrice` | DOUBLE | 当前版本是 | 插件用于每日持仓估值的收盘价；历史 Parquet 可能缺列，且该值不能证明当日可交易 |
| `strategyName` | STRING | 否 | 模拟交易模式可能出现；Arena 当前历史回测不依赖 |

这是盘后时点表，不是完整的“全部代码 × 全部日期”矩阵；零仓行可能存在，也可能缺行。结果列以
实际 Parquet schema 为准：当前验证的 `2.00.16.32` 输出 `closePrice`，升级前生成的历史文件可能
缺少该列。不得把 `longPositionAvgPrice` 当作当日估值价。

当前部署已观察到 `todaySellVolume` 和 `todaySellValue` 不能可靠反映真实卖出，因此不得用它们单独
审计卖出，也不得强制使用它们验证持仓数量恒等式。卖出审计以
`daily_trading_statistics.todaySellCloseTradeVolume/Value` 为主，并结合 `trade_details` 的订单方向、
累计成交字段和终态核对。

停牌或必要价格缺失时，持仓仍可能存在，`closePrice` 也可能沿用最近估值价格；它不能证明该证券
当日存在可交易 message，不能用它或持仓均价回填因子数据。

## `daily_portfolios`

每个有效回测交易日一行。字段字典：

| 字段 | 类型 | 必需 | 口径 |
| --- | --- | --- | --- |
| `tradeDate` | DATE | 是 | 盘后交易日期 |
| `cash` | DOUBLE | 是 | 盘后可用现金 |
| `totalMarketValue` | DOUBLE | 是 | 盘后持仓总市值 |
| `totalEquity` | DOUBLE | 是 | 盘后账户总权益 |
| `netValue` | DOUBLE | 是 | 截至当日的累计净值 |
| `totalReturn` | DOUBLE | 是 | 截至当日的累计收益率 |
| `ratio` | DOUBLE | 是 | 当日相对前一有效组合日的收益率 |
| `pnl` | DOUBLE | 是 | 当日相对前一有效组合日的权益变化 |
| `frozenFunds` | DOUBLE | 是 | 盘后冻结资金 |
| `totalFee` | DOUBLE | 是 | 截至当日的累计费用，不是当日费用 |
| `floatingPnl` | DOUBLE | 是 | 截至当日的浮动盈亏 |
| `realizedPnl` | DOUBLE | 是 | 截至当日的已实现盈亏 |
| `totalPnl` | DOUBLE | 是 | 截至当日的总盈亏 |
| `benchmarkClosePrice` | DOUBLE | 否 | 配置 `benchmark` 后返回的基准指数当日收盘价 |
| `benchmarkNetValue` | DOUBLE | 否 | 配置 `benchmark` 后由插件计算的基准净值 |
| `bottomNetValue` | DOUBLE | 否 | 配置底仓时的底仓净值；普通 Arena 请求不依赖 |
| `strategyName` | STRING | 否 | 模拟交易模式可能出现；Arena 当前历史回测不依赖 |

当前日线合成模式使用浮点容差检查：

```text
totalEquity = cash + totalMarketValue
netValue = totalEquity / initialCash
totalReturn = netValue - 1
totalPnl = floatingPnl + realizedPnl
totalEquity = initialCash + totalPnl
ratio[t] = netValue[t] / netValue[t-1] - 1
pnl[t] = totalEquity[t] - totalEquity[t-1]
feeIncrement[t] = totalFee[t] - totalFee[t-1]
```

首行前值使用 `initialCash`、净值 1 和累计费用 0。费用已经进入现金和权益，不能再次从权益扣除。
若存在公司行为、冻结资金或其他现金事件，现金变化检查必须加入相应现金流。

配置 `config.benchmark` 后，网页使用 `benchmarkNetValue` 与策略净值按当前筛选区间首个有效值重新
归一后绘制对比曲线；原始 Parquet 仍保留插件返回值，不会被网页覆写。基准行情来自同一
`coreTable`、同一闭区间的指数日行情，缺少基准报价的日期不会自行前向填充。

## `daily_trading_statistics`

这是标准必需输出。Runtime 直接调用 `Backtest::getDailyTradingStatistics`；函数不存在或调用失败时
异常原样上抛，工作流失败，不会跳过文件或把缺失结果解释为成功。字段仍应以实际 Parquet Schema
为准。

该表按证券和交易日汇总实际成交。字段字典：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `symbol` | STRING | 证券代码 |
| `tradeDate` | DATE | 交易日期 |
| `todayBuyOpenTradeVolume` | LONG | 当日买开成交量 |
| `todayBuyOpenTradeValue` | DOUBLE | 当日买开成交额 |
| `todayBuyOpenAvgPrice` | DOUBLE | 当日买开成交均价 |
| `todaySellOpenTradeVolume` | LONG | 当日卖开成交量；普通股票多头策略通常为 0 |
| `todaySellOpenTradeValue` | DOUBLE | 当日卖开成交额 |
| `todaySellOpenAvgPrice` | DOUBLE | 当日卖开成交均价 |
| `todaySellCloseTradeVolume` | LONG | 当日卖平成交量 |
| `todaySellCloseTradeValue` | DOUBLE | 当日卖平成交额 |
| `todaySellCloseAvgPrice` | DOUBLE | 当日卖平成交均价 |
| `todayBuyCloseTradeVolume` | LONG | 当日买平成交量；普通股票多头策略通常为 0 |
| `todayBuyCloseTradeValue` | DOUBLE | 当日买平成交额 |
| `todayBuyCloseAvgPrice` | DOUBLE | 当日买平成交均价 |
| `assetType` | STRING | 仅多资产结果可能出现；Arena 当前标准股票结果不依赖 |

完全没有成交统计的 `(symbol, tradeDate)` 可以缺行。以上数值类型是当前标准股票结果的语义类型；
Parquet 的具体整数宽度仍以实际 Schema 为准。

任一方向在成交量大于 0 时应满足：

```text
averagePrice ≈ tradeValue / tradeVolume
```

成交量为 0 时的零均价不是真实成交价格。

## 费用

费用由 DolphinDB Backtest 插件根据 `commission`、`tax` 和最低单笔费用配置计算：

- `commission` 对买卖双方生效，`tax` 只进入卖出费用；
- 最低费用按插件的单笔交易规则处理，不按每日证券汇总；
- `onTrade` 的 `totalFee` 是该成交事件报告的费用；
- `daily_portfolios.totalFee` 是累计值，当日费用取相邻日期差分；
- 合成价差体现在成交价格中，不进入 `totalFee`；
- 部分成交时不得自行假设费用分摊，以实际事件和累计费用为准。

## 最低审计顺序

```text
检查实际输出列表、各表 Schema 和日期
  -> 按 orderId 还原订单生命周期
  -> 用成交统计核对真实方向、量、额和均价
  -> 用持仓表核对盘后数量和无法退出状态
  -> 用组合表核对现金、权益、净值、PnL 和费用
  -> 披露拒单、撤单拒绝、期末未成交和期末持仓
```

指标口径、数据时点、撮合限制和“运行 → QA → 保存”流程见 `arena://docs/backtest/qa`。
