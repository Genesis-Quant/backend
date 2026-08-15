# Backtest 结果数据与审计契约

本文件定义 Arena 当前股票回测四张 Parquet 结果表的行粒度、字段语义、订单事件、费用、对账规则和
已知限制。如何提交请求见 `arena://docs/backtest/request`，行情、回调和撮合见
`arena://docs/backtest/dolphindb`，下载方法见 `arena://docs/overview/workflows`。
可直接运行的下载后复算、账务检查和保存版本顺序见 `arena://docs/backtest/qa`。

字段来源是 Runtime 当前调用的 DolphinDB Backtest 接口：

```text
trade_details             = Backtest::getTradeDetails(engine)
daily_positions           = Backtest::getDailyPosition(engine)
daily_portfolios          = Backtest::getDailyTotalPortfolios(engine)
daily_trading_statistics  = Backtest::getDailyTradingStatistics(engine)
```

官方接口定义见
[Backtest 接口说明](https://docs.dolphindb.com/zh/plugins/backtest/interface_description.html)。插件升级或
配置 `outputOrderInfo`、`outputSeqNum`、`outputTradeSeqNum` 等可改变附加列；读取方应先检查 Parquet
Schema，不能只按固定列位置解析。

## 总览

| 输出 | 行粒度 | 候选键 | 累计值 | 主要用途 |
| --- | --- | --- | --- | --- |
| `trade_details` | 一次订单状态事件 | 没有默认稳定主键；同一 `orderId` 多行 | 部分字段随订单状态更新 | 委托、状态、拒单和成交审计 |
| `daily_positions` | 证券 × 交易日的盘后持仓记录 | `(symbol, tradeDate)` | 持仓量是时点值；买卖量是当日值 | 盘后持仓和估值 |
| `daily_portfolios` | 组合 × 交易日 | `tradeDate` | `totalReturn`、`totalFee`、Pnl 类字段中部分为累计值 | 现金、权益、净值和费用 |
| `daily_trading_statistics` | 有交易统计的证券 × 交易日 | `(symbol, tradeDate)` | 全部是当日值 | 买开、卖平等方向成交审计 |

`NULL`、`0` 和缺行不能互换：

- `NULL` 表示该行存在但字段未知或无有效值；
- `0` 表示插件输出了数值零，但不自动证明“没有业务事件”，应结合本页已知限制；
- 缺行表示该接口没有为该粒度输出记录，不能凭空解释成 `NULL`，聚合时才可按明确口径补零；
- 四张表中的 `timestamp[ns]` 当前没有时区元数据，语义为中国证券交易所本地时间。

## `trade_details`

### 行粒度与字段

该表名称容易误导：它是**订单状态事件表**，不是“一笔订单一行”，也不是纯成交逐笔表。同一
`orderId` 通常至少出现“已报”和“终态”两行。

| 字段 | 当前 Parquet 类型 | 语义 |
| --- | --- | --- |
| `orderId` | INT64 | 订单 ID；用于聚合同一订单的全部状态事件 |
| `symbol` | STRING | `.XSHG/.XSHE` 标准证券代码 |
| `direction` | INT32 | `1` 买开、`2` 卖开、`3` 卖平、`4` 买平 |
| `sendTime` | TIMESTAMP_NS | 委托提交时间，不是成交确认时间 |
| `orderPrice` | DOUBLE | 委托限价 |
| `orderQty` | INT64 | 原始委托数量 |
| `tradeTime` | TIMESTAMP_NS | 插件写入的事件/成交时间；非空本身不代表成交 |
| `tradePrice` | DOUBLE | 当前事件的成交价；无成交事件通常为 `0` |
| `tradeQty` | INT64 | 状态 `0/1` 时表示累计成交数量；状态 `2` 时表示本次撤单成功数量，不是成交数量；不得跨同一订单的事件行直接求和 |
| `orderStatus` | INT32 | 订单状态，见下表 |
| `label` | STRING | 调用者提交订单时传入的业务标签 |
| `orderInfo` | STRING | `outputOrderInfo=true` 时的风控信息；是否存在取决于配置 |
| `seqNum` | 可选 | 仅启用相应插件输出配置时存在，用于稳定事件顺序 |
| `tradeSeqNum` | 可选 | `outputTradeSeqNum=true` 时存在，表示成交订单序列号 |

上表列出当前股票模式的默认列和 Runtime 明确开放的三类附加列。插件升级仍可能改变 Schema；
`outputQueuePosition` 仅适用于官方文档指定的含逐笔行情模式，不属于 Arena 当前 `dataType=1` 合成
快照结果契约。实际读取始终以 Parquet Schema 为准。

当前 `trade_details` **没有费用列**。费用不能从该表直接读取，应使用 `onTrade` 的 `totalFee` 或
`daily_portfolios.totalFee` 的日增量。

### 状态机

| 状态 | 含义 | 是否终态 | 成交判断 |
| ---: | --- | --- | --- |
| `4` | 已报 | 否 | 不是成交 |
| `0` | 部分成交 | 否 | 有成交但仍有未完成数量 |
| `1` | 全部成交 | 是 | 已完成成交 |
| `2` | 撤单成功 | 是 | 未完成部分已撤销；该事件的 `tradeQty` 是撤单成功数量，不是成交数量 |
| `-1` | 审批或风控拒绝 | 是 | 不是成交 |
| `-2` | 撤单拒绝 | 否 | 原订单可能仍活动，必须再查真实挂单/后续事件 |
| `-3` | 回测结束仍未成交 | 是 | 不是成交，也不等于主动撤单 |

常见事件路径：

```text
正常完整成交：4 -> 1
审批拒绝：    4 -> -1
部分成交：    4 -> 0 -> ... -> 1、2 或 -3
撤单成功：    4 -> 2，或 4 -> 0 -> 2
撤单拒绝：    ... -> -2 -> 后续仍需继续观察
```

可靠处理规则：

1. 按 `orderId` 聚合，绝不能把每行当成独立订单；
2. 用状态集合 `{1, 2, -1, -3}` 判断终态，不能按状态码数值大小排序；
3. `tradeTime` 非空但 `tradeQty=0`、`tradePrice=0` 的 `4` 或 `-1` 行不是成交；
4. `label` 只是调用者标签，不代表最终方向。目标仓位函数在降低已有持仓时，即使标签包含 `buy`，
   也可能生成 `direction=3` 的卖平订单；方向只认 `direction`；
5. 默认未输出稳定序号时，Parquet 物理行顺序不构成跨版本业务契约。需要精确事件先后时启用并使用
   插件序号列，或在回调日志中记录订单状态；
6. 不要对同一订单各状态行的 `tradeQty` 求和，否则会把状态快照重复计算；状态 `2` 的
   `tradeQty` 还必须作为撤单量解释；
7. 实际成交优先使用 `onTrade` 事件或 `daily_trading_statistics` 审计；不能把状态 `2` 的撤单量计入
   成交量。

## `daily_positions`

### 行粒度与字段

该表是每日盘后持仓记录。候选键为 `(symbol, tradeDate)`；它不是完整的“所有证券 × 所有交易日”
矩阵，卖出日可能保留一行 `longPosition=0` 的记录，其他无持仓日也可能完全缺行。

| 字段 | 当前 Parquet 类型 | 口径 |
| --- | --- | --- |
| `symbol` | STRING | 证券代码 |
| `tradeDate` | TIMESTAMP_NS | 交易日，时间部分通常为 00:00:00 |
| `lastDayLongPosition` | INT64 | 上一交易日盘后多头数量 |
| `lastDayShortPosition` | INT64 | 上一交易日盘后空头数量；普通 A 股多头回测通常为 0 |
| `longPosition` | INT64 | 当日盘后多头数量，时点值 |
| `longPositionAvgPrice` | DOUBLE | 当日盘后多头持仓均价，执行价格尺度 |
| `shortPosition` | INT64 | 当日盘后空头数量 |
| `shortPositionAvgPrice` | DOUBLE | 当日盘后空头持仓均价 |
| `todayBuyVolume` | INT64 | 插件在该持仓记录中报告的当日买入量 |
| `todayBuyValue` | DOUBLE | 插件在该持仓记录中报告的当日买入额 |
| `todaySellVolume` | INT64 | 插件在该持仓记录中报告的当日卖出量 |
| `todaySellValue` | DOUBLE | 插件在该持仓记录中报告的当日卖出额 |
| `closePrice` | DOUBLE | 插件用于盘后估值的执行价格尺度收盘价 |

### 当前已知限制

当前部署版本已观察到 `todaySellVolume` 和 `todaySellValue` 全为 0，但同一结果中存在真实卖出订单和
卖平成交。因此这两个字段目前不能作为卖出审计依据，也不能用它们强制验证下面的理论恒等式：

```text
longPosition = lastDayLongPosition + todayBuyVolume - todaySellVolume
```

卖出量和卖出额应以 `daily_trading_statistics.todaySellCloseTradeVolume/Value` 为主，并用
`trade_details` 的 `direction=3` 成交终态交叉核对。

停牌或必要价格缺失时，当日不会生成该证券的合成快照；持仓仍可能在本表出现，`closePrice` 可能沿用
插件最近可用估值价。这个价格不是当日新行情。因子查询中的当日价格仍可为 `NULL`，不得用持仓
`closePrice` 回填因子数据并声称是当日收盘。

## `daily_portfolios`

### 行粒度与字段

该表每个有效回测交易日一行，是组合级净值和现金的主要数据源。

| 字段 | 当前 Parquet 类型 | 当日/累计 | 语义 |
| --- | --- | --- | --- |
| `tradeDate` | TIMESTAMP_NS | 时点 | 交易日 |
| `floatingPnl` | DOUBLE | 累计时点 | 当前未平仓浮动盈亏 |
| `realizedPnl` | DOUBLE | 累计时点 | 截至当日已实现盈亏；当前部署实测已反映交易费用 |
| `totalPnl` | DOUBLE | 累计时点 | 总盈亏 |
| `cash` | DOUBLE | 时点 | 当日盘后可用现金 |
| `totalMarketValue` | DOUBLE | 时点 | 当日盘后持仓总市值 |
| `totalEquity` | DOUBLE | 时点 | 当日盘后账户总权益 |
| `netValue` | DOUBLE | 累计时点 | 相对初始资金的单位净值 |
| `totalReturn` | DOUBLE | 累计 | 截至当日累计收益率 |
| `ratio` | DOUBLE | 当日 | 当日净值收益率 |
| `pnl` | DOUBLE | 当日 | 相对上一有效组合日的权益变化；首日相对初始资金 |
| `totalFee` | DOUBLE | 累计 | 截至当日累计交易费用，不是当日费用 |
| `frozenFunds` | DOUBLE | 时点 | 冻结资金 |

当前 Arena 禁止在 `config` 中传入 `benchmark`，因此这张表没有基准价格、基准净值或基准收益列，
Backend 也不会自动计算基准指标。需要比较基准时，应在结果下载后以明确的数据版本和费用口径独立计算。

标准对账：

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

首行的前值分别使用 `netValue=1`、`totalEquity=initialCash`、`totalFee=0`。浮点对账应使用容差，
不能要求二进制浮点完全相等。

上述 `totalEquity = cash + totalMarketValue` 适用于当前日线合成模式的盘后结果。若使用其他 Runtime
模式并在盘后仍存在 `frozenFunds`，应先按该模式的插件账户定义确认冻结资金是否已包含在 `cash`，
不能未经核对重复加减。

在无分红、配股、利息、冻结资金变化和其他现金事件的普通股票日期，可进一步检查：

```text
cash[t] - cash[t-1]
  = 当日卖出成交额 - 当日买入成交额 - feeIncrement[t]
```

存在公司行为或其他现金事件时必须把对应现金流加入右侧，不能把差额一律判成错误。费用已经进入
现金和盈亏；对 `totalEquity` 再减一次 `totalFee` 会重复扣费。

## `daily_trading_statistics`

### 行粒度与字段

该表按证券和交易日汇总实际成交，是当前卖出审计的首选表。无该方向成交时字段为 0；完全没有交易
统计的 `(symbol, tradeDate)` 可能缺行。

| 字段 | 当前 Parquet 类型 | 语义 |
| --- | --- | --- |
| `symbol` | STRING | 证券代码 |
| `tradeDate` | TIMESTAMP_NS | 交易日 |
| `todayBuyOpenTradeVolume` | INT64 | 当日买开成交量 |
| `todayBuyOpenTradeValue` | DOUBLE | 当日买开成交额 |
| `todayBuyOpenAvgPrice` | DOUBLE | 当日买开均价 |
| `todaySellOpenTradeVolume` | INT64 | 当日卖开成交量；普通 A 股多头回测通常为 0 |
| `todaySellOpenTradeValue` | DOUBLE | 当日卖开成交额 |
| `todaySellOpenAvgPrice` | DOUBLE | 当日卖开均价 |
| `todaySellCloseTradeVolume` | INT64 | 当日卖平成交量 |
| `todaySellCloseTradeValue` | DOUBLE | 当日卖平成交额 |
| `todaySellCloseAvgPrice` | DOUBLE | 当日卖平平均价 |
| `todayBuyCloseTradeVolume` | INT64 | 当日买平成交量 |
| `todayBuyCloseTradeValue` | DOUBLE | 当日买平成交额 |
| `todayBuyCloseAvgPrice` | DOUBLE | 当日买平平均价 |

任一方向在 `volume > 0` 时应满足：

```text
averagePrice ≈ tradeValue / tradeVolume
```

在 `volume=0` 时，当前插件通常输出 `value=0`、`averagePrice=0`；不要把零均价当作真实成交价。

## 费用契约

Arena 不在 Python 或 DOS 中重新计算费用，费用由 DolphinDB Backtest 插件根据 `commission`、`tax`
和 `enableMinimumPerTransactionFee` 计算。当前股票合成盘口中，一笔委托一次完整成交时的已验证行为：

```text
买入费用 = enableMinimumPerTransactionFee
         ? max(5, 成交额 * commission)
         : 成交额 * commission

卖出费用 = enableMinimumPerTransactionFee
         ? max(5, 成交额 * (commission + tax))
         : 成交额 * (commission + tax)
```

- `commission` 买卖双方收取；`tax` 只进入卖出费用；
- 最低 5 元由插件按“每笔交易”规则执行，不是每日证券汇总最低 5 元；
- `onTrade` 中每个 `event["totalFee"]` 是该成交事件报告的费用；
- `daily_portfolios.totalFee` 是组合累计费用，日费用必须取差分；
- `syntheticSpread` 已反映在买一/卖一成交价格中，不进入 `totalFee`；
- 当前部署实测中，费用会减少现金并反映到已实现盈亏和总权益；不能在报告层重复扣除。

当前近似无限一档盘口通常使合规目标订单一次完整成交。若因盘口、风控或其他原因出现部分成交，
`trade_details` 没有费用列，且不能假设最低费用按每日汇总；必须以每次 `onTrade` 的实际 `totalFee`
和 `daily_portfolios.totalFee` 增量为准。

## 三类标准审计样例

以下是字段关系的**示意行**，数值和 ID 只用于解释表语义，不对应任何具体策略或历史运行。

### 普通无交易日

| 输出 | 示意 |
| --- | --- |
| `trade_details` | 没有该日事件行 |
| `daily_positions` | `{lastDayLongPosition: 100, longPosition: 100, todayBuyVolume: 0, todaySellVolume: 0}`，已有持仓继续出现 |
| `daily_portfolios` | `{totalFee: F[t-1], ratio: 当日权益变化率}`；`feeIncrement=0` |
| `daily_trading_statistics` | 可以没有该证券/日期行；若有行，各方向 volume/value 为 0 |

### 同时买卖的日期

| 输出 | 示意 |
| --- | --- |
| `trade_details` | 买单 `orderId=101: (4 -> 1, direction=1)`；卖单 `orderId=102: (4 -> 1, direction=3)`，每个订单保留全部状态事件 |
| `daily_positions` | 买入证券的 `longPosition` 增加；卖出证券可保留 `longPosition=0` 行；当前部署不使用 `todaySell*` 审计卖出 |
| `daily_portfolios` | `feeIncrement=totalFee[t]-totalFee[t-1]`；无其他现金事件时按成交额与费用对账现金变化 |
| `daily_trading_statistics` | 买入证券 `todayBuyOpenTradeVolume>0`，卖出证券 `todaySellCloseTradeVolume>0`，均价约等于 value/volume |

按 `orderId` 检查真实 `direction` 和终态，不使用 `label` 猜方向。

### 拒单、停牌或零持仓

| 输出 | 示意 |
| --- | --- |
| `trade_details` | 拒单为同一订单 `(4 -> -1)` 且 `tradeQty=0, tradePrice=0`；没有可提交订单时也可能完全无行 |
| `daily_positions` | 零仓位日期可能保留 `longPosition=0` 行；缺价但仍持仓时可继续显示旧 `closePrice` |
| `daily_portfolios` | 拒单不会增加成交费用；缺价持仓的权益可能沿用插件最近估值，必须结合持仓表解释 |
| `daily_trading_statistics` | 拒单或无快照不产生实际成交统计，因而可以缺行 |

必要价格缺失导致证券不在当日 message 时，不会产生可交易快照；恢复交易后 Runtime 也不会自动
改变目标仓位，只有后续实际订单和成交事件才能改变持仓。

## SUCCESS 后的最低 QA

工作流 `SUCCESS` 只表示程序完成。采信报告前至少检查：

1. 日期严格递增，时间戳按交易所本地时间解释；
2. 信号日期严格早于决策日期，价格型信号和成交/持仓价格处于同一复权尺度；
3. 每个 `orderId` 都按事件状态机处理，拒单、撤单拒绝、期末未成交分别统计；
4. 以 `daily_trading_statistics` 和 `direction` 对账买卖，不依赖标签或已知异常字段；
5. 验证组合权益、净值、收益、Pnl 和累计费用公式；
6. 检查停牌、缺价、无法退出和期末未平仓；Arena 不为报表自动平仓；
7. 检查缺失值、每日可交易代码数和成交覆盖率；
8. 固定 `annual_trading_days`、`risk_free_rate`、费用、复权方式和期末持仓口径后再比较结果。

### 后端项目报告指标

项目版本摘要和批量研究当前由 Backend 直接读取 `daily_portfolios.ratio`：

- 先按 `tradeDate` 排序；`ratio` 的 NULL、无法转为数值和非有限值按 0 处理；
- 累计收益按 `cumprod(1 + ratio) - 1`；年化收益使用全部 `ratio` 行数和
  `annual_trading_days` 几何年化；
- 年化波动使用**从第二行开始**的 `ratio` 总体标准差乘 `sqrt(annual_trading_days)`；
- Sharpe 为 `(annualReturn - risk_free_rate) / annualVolatility`；
- Sortino 先把年化无风险收益换成单期收益，再使用全序列负超额收益计算下行波动；
- 胜率是非零 `ratio` 中正收益日比例，不是逐笔交易胜率；
- 最大回撤从初始财富 1.0 开始计算，返回非正数；
- 总费用由 `totalFee` 的非负日增量求和；当前请求禁止传入 `benchmark`，不自动计算基准收益。

Runtime 还提供一个**可选** `return_summary` 输出：它从非 NULL `netValue` 的相邻变化重算部分插件
摘要字段。该文件不属于网页项目默认请求的四张结果表，计算输入和缺失值处理也不同，不能把它与
Backend 项目报告指标混称为同一口径。比较结果时应先确认实际读取的是哪一种摘要。
