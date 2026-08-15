# Backtest 结果与审计契约

本页定义当前四张 Parquet 的行粒度、关联字段、订单状态和账务口径。附加列可能随插件版本及
`outputOrderInfo`、`outputSeqNum`、`outputTradeSeqNum` 配置变化，读取时必须先检查实际 Schema。

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

## `trade_details`

该表是订单状态事件表，不是“一笔订单一行”，也不是纯成交明细。同一 `orderId` 可同时存在创建、
更新和终态事件。

核心字段：

| 字段 | 语义 |
| --- | --- |
| `orderId` | 订单 ID；聚合同一订单的全部事件 |
| `symbol`、`direction` | 证券和真实订单方向 |
| `sendTime`、`orderPrice`、`orderQty` | 委托时间、价格和数量 |
| `tradeTime`、`tradePrice`、`tradeQty` | 当前状态事件中的成交相关字段；不能跨事件行直接求和 |
| `orderStatus` | 订单状态 |
| `label` | 调用方标签；不能代替 `direction` |
| `orderInfo` | 可选风控文本；仅在配置和插件实际输出时存在，不是稳定结构化错误 |
| `seqNum`、`tradeSeqNum` | 可选序号；存在时用于稳定事件顺序 |

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
- 实际成交优先使用 `onTrade` 或 `daily_trading_statistics`；
- 默认没有序号列时，Parquet 物理行顺序不是稳定业务顺序；
- 当前 MCP 不合成目标金额、可用现金和具体拒绝规则。拒单详情仅在实际 `orderInfo` 或日志可用时辅助
  解释，不能假定每笔拒单都有完整原因。

`trade_details` 当前没有费用列。费用从 `onTrade.totalFee` 或 `daily_portfolios.totalFee` 的日增量读取。

## `daily_positions`

核心字段包括 `symbol`、`tradeDate`、昨日/当日多空数量、持仓均价、当日买卖量额和 `closePrice`。
这是盘后时点表，不是完整的“全部代码 × 全部日期”矩阵；零仓行可能存在，也可能缺行。

当前部署已观察到 `todaySellVolume` 和 `todaySellValue` 不能可靠反映真实卖出，因此不得用它们单独
审计卖出，也不得强制使用它们验证持仓数量恒等式。卖出应以
`daily_trading_statistics.todaySellCloseTradeVolume/Value` 为主，再与订单方向和终态交叉核对。

停牌或必要价格缺失时，持仓仍可能存在，`closePrice` 可能沿用最近可用估值价；它不代表当日新行情，
不能回填为因子数据。

## `daily_portfolios`

每个有效回测交易日一行。核心字段及口径：

| 字段 | 口径 |
| --- | --- |
| `cash`、`totalMarketValue`、`totalEquity`、`frozenFunds` | 盘后时点值 |
| `floatingPnl`、`realizedPnl`、`totalPnl` | 截至当日的盈亏时点值 |
| `netValue`、`totalReturn` | 截至当日的累计净值和收益 |
| `ratio`、`pnl` | 当日相对前一有效组合日的收益和权益变化 |
| `totalFee` | 截至当日的累计费用，不是当日费用 |

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

当前禁止传入 `config.benchmark`，本表没有基准序列或基准指标。需要比较时，应下载后按明确的数据
版本、日期轴和缺失值口径独立计算。

## `daily_trading_statistics`

该表按证券和交易日汇总实际成交。核心字段按买开、卖开、卖平和买平分别提供成交量、成交额和均价。
完全没有成交统计的 `(symbol, tradeDate)` 可以缺行。

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
检查四表 Schema 和日期
  -> 按 orderId 还原订单生命周期
  -> 用成交统计核对真实方向、量、额和均价
  -> 用持仓表核对盘后数量和无法退出状态
  -> 用组合表核对现金、权益、净值、PnL 和费用
  -> 披露拒单、撤单拒绝、期末未成交和期末持仓
```

指标口径、数据时点、撮合限制和“运行 → QA → 保存”流程见 `arena://docs/backtest/qa`。
