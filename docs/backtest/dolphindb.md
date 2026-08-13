# DolphinDB Backtest 运行契约

本页只描述 Arena Runtime 当前实际创建的股票回测环境，不是 DolphinDB 语言教程，也不覆盖插件
其它 dataType 或 matchingMode。策略可以依赖本文列出的 message、Runtime helper 和回调事件结构。

## 官方文档

本文对 Arena 固定配置进行具体化，DolphinDB 插件的通用定义以以下官方文档为准：

- [股票回测配置](https://docs.dolphindb.com/zh/plugins/backtest/stock.html)：`dataType`、
  `matchingMode`、快照 message、费用、延时、撮合比例和股票配置；
- [Backtest 接口说明](https://docs.dolphindb.com/zh/plugins/backtest/interface_description.html)：
  `submitOrder`、`cancelOrder`、`getPosition`、`getOpenOrders`、回调事件和结果结构；
- [模拟撮合引擎使用教程](https://docs.dolphindb.com/zh/tutorials/matching_engine_simulator.html)：
  价格优先/时间优先、快照订单到达后的即时撮合、模式 1/2 和未成交订单处理；
- [Backtest 插件总览](https://docs.dolphindb.com/zh/plugins/backtest.html)：插件版本、安装、引擎和
  各资产文档入口。

官方文档描述插件支持的全部配置；下文写的是 Arena Runtime 在这些能力中固定选择的子集。如果两
者看似不同，应先检查 Arena 是否固定了某个参数，而不是把官方的其它模式套到当前运行环境。

## 运行时字段自省

DolphinDB 没有一个返回 Backtest 所有可用字段的统一接口。必须按对象的数据形式查询：

| 对象 | 获取字段的方法 | 限制 |
| --- | --- | --- |
| `message` | `schema(message).colDefs` 或 `columnNames(message)` | Arena 固定 `msgAsTable=true`，可在回调中检查当前真实表 |
| 不指定 symbol 的持仓 | `schema(Backtest::getPosition(context.engine)).colDefs` | 返回表 |
| 指定 symbol 的持仓 | `Backtest::getPosition(context.engine, stockCode).keys()` | 返回字典，只有 key，没有独立 Schema |
| 挂单 | 对 `Backtest::getOpenOrders(context.engine)` 的表结果使用 `schema(...).colDefs` | 插件也可能返回由字典组成的 tuple；空结果和返回 form 必须先判断 |
| 成交/每日结果 | `schema(Backtest::getTradeDetails(engine)).colDefs` 等 | 需要已创建的 engine；可选配置会增加列 |
| 订阅指标 | `Backtest::getIndicatorSchema(engine, "snapshot")` | 只返回订阅指标表结构，不返回 message、订单或成交事件结构 |
| `onOrder` / `onTrade` | 无字段名自省接口 | Arena 当前传入位置型 ANY VECTOR，只能依照当前配置下的固定位置契约 |

表的通用 `schema` 返回 `colDefs`，包含 `name`、`typeString`、`typeInt` 等信息，官方说明见
[DolphinDB schema](https://docs.dolphindb.com/zh/funcs/s/schema.html)。例如：

```dos
def onSnapshot(mutable context, message, indicator) {
    print(schema(message).colDefs)
}

positions = Backtest::getPosition(context.engine)
print(schema(positions).colDefs)

position = Backtest::getPosition(context.engine, stockCode)
print(position.keys())
```

`outputOrderInfo`、`outputSeqNum`、`outputTradeSeqNum`、`outputQueuePosition` 等配置会改变部分结果或
事件的附加字段。因此“全部字段”必须绑定到当前 engine 配置；官方静态字段表和运行时自省应一起
使用。Arena 文档下面列出的 message 与事件位置，针对的是 Runtime 当前固定配置。

DolphinDB 普通内置函数的部署版本签名可用 `describe_dolphindb_functions` 查询。带
`Backtest::` 或 `backtest::` 命名空间的接口不要传给该工具，其契约以本页为准。

## 固定引擎配置

官方字段定义见 [股票回测配置](https://docs.dolphindb.com/zh/plugins/backtest/stock.html)。Arena
Runtime 不将这些字段开放给请求，而是在创建引擎前强制写入下列值：

Runtime 强制设置：

| 配置 | 值 |
| --- | --- |
| `strategyGroup` | `"stock"` |
| `dataType` | `1` |
| `matchingMode` | `1` |
| `frequency` | `0` |
| `callbackForSnapshot` | `0` |
| `msgAsTable` | `true` |
| `msgAsPiecesOnSnapshot` | `true` |
| `matchingRatio` | `0.0` |
| `orderBookMatchingRatio` | `1.0` |

`startDate` 和 `endDate` 来自 `dataset_query`。这些值不是用户可调参数，出现在 `config` 中会被
BacktestParameters 拒绝。当前模式使用快照回调，不生成 bar，因此 `onSnapshot` 会触发，`onBar`
不会触发但仍必须定义。

## 日线生成合成快照

DolphinDB 对 dataType=1 快照表的字段要求见
[股票回测配置](https://docs.dolphindb.com/zh/plugins/backtest/stock.html)。Arena 使用日线自行生成
符合该结构的两条快照；价格和盘口生成规则属于 Arena Runtime 契约，不是 DolphinDB 自动行为。

Runtime 从 `dataset_query` filters 后的表生成消息。每个有效交易日、每只代码生成两条快照：

| 时间 | `lastPrice` |
| --- | --- |
| `09:30:00` | 当日 `open` |
| `15:00:00` | 当日 `close` |

`adj=null` 不复权；`hfq` 使用 `adj_factor` 后复权；`qfq` 使用每只代码最后一个 `adj_factor` 归一
后前复权。`open/low/high/close/upLimitPrice/downLimitPrice/prevClosePrice` 使用相同调整系数。必要
价格为空的日线不会生成快照。

### 原始历史数据与执行价格是两套尺度

`dataset_query` 计算出的 factors、derivatives、`getHistoryData` 和 `getLastData` **始终保留
CoreData 原始价格尺度**。`adj` 不会改写这些历史列；它只调整 message 以及随后产生的订单、成交和
持仓价格。因此 `adj="qfq"` 或 `"hfq"` 时，下面的直接比较是错误的，虽然工作流通常仍会成功：

```dos
// 错误：raw ATR 与 adjusted position price 处在两个尺度
stopPrice = position.longPositionAvgPrice - 2.0 * signal.atr_raw[0]
```

在 t 日开盘回调中，`signal` 是同一代码的前一实际交易日行时，可使用 message 的复权昨收与该行
原始收盘得到当前执行尺度比例：

```dos
rawClose = double(signal.close[0])
scale = double(message.prevClosePrice[rowIndex]) / rawClose
atrExecution = double(signal.atr_raw[0]) * scale
channelExecution = double(signal.entry_channel_raw[0]) * scale
```

使用前必须确认代码一致、两端非 NULL 且大于 0。若信号行不是该代码的前一实际交易日（停牌、缺
行或手工拼表），不能套用该比例，应跳过该次交易。所有价格型指标——ATR、通道、均线、止损距
离、风险仓位分母——都必须先转到 message/持仓的执行价格尺度。

代码在查询完成后统一映射：

```text
.SZ -> .XSHE
.SH -> .XSHG
```

message 和 Runtime 历史数据 helper 返回的 `code` 都使用 `.XSHE/.XSHG`。

合成盘口只有一档：

```text
bidPrice[0]   = round(lastPrice * (1 - syntheticSpread / 2), 3)
offerPrice[0] = round(lastPrice * (1 + syntheticSpread / 2), 3)
bidQty[0] = offerQty[0] = totalBidQty = totalOfferQty = 1,000,000,000
```

`syntheticSpread` 是完整相对价差，默认 0，范围 `[0,1)`。十亿股/份表示近似无限盘口，不使用
当日 `volume` 限制成交。

### 当前订单簿的实际结构

每个 symbol、每个快照只有一档，ARRAY VECTOR 的外层按档位、内层按 message 行索引：

```text
                     买方订单簿                         卖方订单簿
档位 0     bidPrice[0][row], bidQty[0][row]   offerPrice[0][row], offerQty[0][row]
数量       1,000,000,000                      1,000,000,000
档位 1+    不存在                             不存在
```

当 `syntheticSpread=0.001`、`lastPrice=10` 时，买一为 9.995，卖一为 10.005；当 spread=0 时，
买一、卖一和 lastPrice 都是 10。`upLimitPrice/downLimitPrice` 是独立风控边界，不是第二档盘口。

## `onSnapshot` message

同一时间戳的全部股票作为一张表触发一次 `onSnapshot`。列为：

| 列 | DolphinDB 类型 | 说明 |
| --- | --- | --- |
| `symbol` | SYMBOL | `.XSHG/.XSHE` 代码 |
| `symbolSource` | SYMBOL | `XSHG` 或 `XSHE` |
| `timestamp` | TIMESTAMP | 09:30 或 15:00 |
| `lastPrice` | DOUBLE | 当前 open/close 合成价格 |
| `upLimitPrice` | DOUBLE | 同复权口径涨停价 |
| `downLimitPrice` | DOUBLE | 同复权口径跌停价 |
| `totalBidQty` | LONG | 总买盘数量 |
| `totalOfferQty` | LONG | 总卖盘数量 |
| `bidPrice` | DOUBLE ARRAY VECTOR | 一档买价，读取方式 `bidPrice[0][row]` |
| `bidQty` | LONG ARRAY VECTOR | 一档买量 |
| `offerPrice` | DOUBLE ARRAY VECTOR | 一档卖价 |
| `offerQty` | LONG ARRAY VECTOR | 一档卖量 |
| `prevClosePrice` | DOUBLE | 同复权口径昨收 |

```dos
def onSnapshot(mutable context, message, indicator) {
    if (message.rows() == 0) return
    currentClock = time(message.timestamp[0])
    for (rowIndex in 0..(message.rows() - 1)) {
        stockCode = message.symbol[rowIndex]
        lastPrice = double(message.lastPrice[rowIndex])
        bid1 = double(message.bidPrice[0][rowIndex])
        offer1 = double(message.offerPrice[0][rowIndex])
    }
}
```

message 不包含 `dataset_query.derivatives`。例如 `message.momentum_20d` 无效。策略信号必须通过
下面的历史数据 helper 读取。当前 Runtime 没有向 `indicator` 注册 DSL derivative。
`indicator` 在当前模式下没有供策略使用的注册字段，不应从中猜测信号结构。

## 回放和撮合时点

每个交易日依次回放 09:30 和 15:00 快照。没有时间判断的 `onSnapshot` 每天执行两次。

引擎先接收当前行情并更新最新盘口，再调用该行情的 `onSnapshot`。快照模式下，回调中到达的用户
订单会与引擎保存的最新盘口即时匹配；`latency=0` 时可以在同一个 timestamp 成交。未成交部分才
等待后续行情或撤单。订单是否成交仍取决于限价、涨跌停、可用资金、可卖持仓、延时、费用和插件
风控。`submitOrder` 返回订单号不表示成交；以 `onTrade` 和 `trade_details` 为准。

### `matchingMode=1` 在 Arena 当前固定比例下如何撮合

模式 1 的官方定义及示例见
[模拟撮合引擎使用教程](https://docs.dolphindb.com/zh/tutorials/matching_engine_simulator.html)。

模式 1 原本可以使用最新成交价和对手方订单簿两条撮合路径。Arena 固定：

```text
matchingRatio = 0.0            # 最新成交价/区间成交量路径不分配成交量
orderBookMatchingRatio = 1.0   # 使用对手方订单簿数量的 100%
```

因此当前实际只按一档合成订单簿撮合，不会按 `lastPrice` 或日线 `volume` 另外分配成交量。对一笔
`latency=0` 的普通限价单，处理顺序为：

1. `submitOrder` 在 t 时刻到达撮合引擎，读取该 symbol 最近一条快照的买一/卖一；
2. 买开（direction=1）：只有 `limitPrice >= offerPrice[0]` 才能吃卖一，成交价为卖一；
3. 卖平（direction=3）：只有 `limitPrice <= bidPrice[0]` 才能吃买一，成交价为买一；
4. 本次盘口容量为对应一档数量乘 `orderBookMatchingRatio`，当前即近似十亿股；
5. 仍需通过涨跌停、现金、可卖数量、交易费用和插件风控；
6. 可成交部分立即产生 `onTrade`，剩余数量保留为 open order；
7. 后续 15:00、下一交易日 09:30 等快照到达时，剩余订单用**新快照的盘口**重新执行上述判断，
   直至全成、撤单、拒绝或回测结束成为 `-3`。

多笔订单同时可成交时仍遵循价格优先、同价时间优先。`latency>0` 时，订单到达时间是委托时间加
延时；当前行情只有 09:30/15:00 两个触发点，若延时后的时刻没有新行情，订单会在不早于到达时间
的下一条行情事件中处理，不能再声称于原 timestamp 成交。

Runtime 的 `backtest::order_target` 买入限价直接取当前 `offerPrice[0]`，卖出限价直接取当前
`bidPrice[0]`。所以在 `latency=0`、一档数量足够且通过账户/风控检查时，它提交的订单对当前盘口
是可成交限价单，通常在当前回调 timestamp 成交。`order_target_value` 只是先用 `lastPrice` 换算
目标股数，再调用同一函数；`lastPrice` 是仓位换算分母，不是最终撮合价。

在 09:30 使用前一交易日信号是常见用法：

```dos
if (time(message.timestamp[0]) != 09:30:00.000) return
signal = backtest::getLastData(context, message, false)
```

当 `latency=0` 且限价覆盖当前对手盘时，新订单可以立即使用 09:30 最新盘口成交。若未成交则继续
挂起并等待 15:00 或更晚行情；15:00 回调提交但未立即成交的订单，当日没有下一条合成快照。
订单 timestamp 与成交 timestamp 可以相同，但仍不能把“已提交”当作“已成交”。

## 历史数据 helper

```text
backtest::getHistoryData(context, msg, filter=true)
backtest::getLastData(context, msg, filter=true)
```

- `filter=false`：读取 derivatives 计算后、`dataset_query.filters` 执行前的表；
- `filter=true`：读取 filters 后的表；
- 两者只返回 `date(time) < date(msg.timestamp[0])`；
- `getLastData` 从历史中取得最后一个实际存在的 `time` 截面；
- 返回表含 `dataset_query` 请求的 factors 和命名 derivatives；
- 返回代码已经转换为 `.XSHG/.XSHE`。

`getLastData` 可能返回空表，使用前检查 `rows()`。禁止直接访问 Runtime 会话内部表，禁止使用
`context["coreBacktestComputedData"]`、`context["coreBacktestUnfilteredFactorData"]` 等名称。
这些不是策略 API，且绕开日期边界会引入未来数据。

### 信号日期与 rolling/shift

在 t 日 09:30 调用 `getLastData`，读到的是最后一个 `date(time) < t` 的完整截面，通常是 t-1：

```text
t-20 ... t-2  t-1       t 日 09:30 callback / submitOrder
|------ rolling window ------|  读取 t-1 信号；latency=0 时可与 t 日开盘最新盘口即时撮合
```

- 在 t-1 行计算 `rollingMax(high, 20)` 已包含截至 t-1 的 20 个已完成观测，供 t 日决策时不需要再
  shift；再 shift 会额外少算一天。
- 若在**同一 DSL 行**比较该行 `close/high` 与 20 日通道，通道必须 shift 1，才能排除当前行自身。
- 负 shift、未来收益标签或在 09:30 使用当日 high/low/close 都是未来数据。
- `getLastData` 取最后实际存在日期，不保证自然日相邻；依赖严格连续交易日时需检查 signal.time。

## 参数与 context

```text
getParams() -> parameters.params 对应的 DolphinDB 字典
```

```dos
def initialize(mutable context) {
    params = getParams()
    context["rebalanceDays"] = long(params["rebalanceDays"])
    context["capitalRatio"] = double(params["capitalRatio"])
    context["tradeCount"] = 0l
}
```

插件提供 `context.engine`。策略自己的状态必须在 `initialize` 中创建。Arena 不向 context 注入
Factor 表、message、params 或任何 `coreBacktest*` 变量。

每个任务使用独立 DolphinDB session。`utils` 中定义的函数和顶层变量只在本次任务有效。

## 八个回调

| 名称 | 固定签名 | 当前模式 |
| --- | --- | --- |
| `initialize` | `def initialize(mutable context)` | 引擎创建时一次 |
| `beforeTrading` | `def beforeTrading(mutable context)` | 每个交易日前 |
| `onBar` | `def onBar(mutable context, message, indicator)` | 不触发，仍必须定义 |
| `onSnapshot` | `def onSnapshot(mutable context, message, indicator)` | 每个合成快照触发 |
| `onOrder` | `def onOrder(mutable context, orders)` | 订单状态变化时 |
| `onTrade` | `def onTrade(mutable context, trades)` | 成交时 |
| `afterTrading` | `def afterTrading(mutable context)` | 每个交易日结束后 |
| `finalize` | `def finalize(mutable context)` | 回测结束时一次 |

callbacks JSON 必须恰好包含这八项。未使用回调可以返回 NULL。

## Runtime 目标仓位函数

### `backtest::order_target`

```text
order_target(mutable context, msg, stockCode, targetAmount, orderLabel="order_target")
```

- `stockCode` 必须存在于当前 message；
- `targetAmount` 是非负整数目标股数，不是本次买卖差额；
- 函数用 `Backtest::getPosition` 读取真实多头持仓并计算差额；
- 买入限价使用当前 `offerPrice[0]`，卖出限价使用当前 `bidPrice[0]`；
- 目标 0 精确清仓；
- 无需调整返回 NULL，否则返回订单号。

### `backtest::order_target_value`

```text
order_target_value(mutable context, msg, stockCode, targetValue, orderLabel="order_target_value")
```

- 使用当前快照 `lastPrice` 将非负目标市值换算为目标股数；
- 增减仓数量按 100 股向下取整；
- `targetValue=0` 精确清仓；
- 不做组合层资金预算，不保证多只买单合计小于可用现金；
- 内部继续调用 `order_target`。

组合调仓应先提交卖出目标，再提交买入目标，并为费用和价格变动保留现金。

## 直接提交普通限价单

Runtime helper 无法表达订单时可调用：

```dos
orderId = Backtest::submitOrder(
    context.engine,
    (stockCode, message.timestamp[0], 5, limitPrice, quantity, direction),
    "orderLabel"
)
```

六项依次为：证券代码、时间、价格类型 5、限价、正整数股数、方向。股票多头普通订单使用方向
`1` 买开、`3` 卖平。合成执行价差由 `syntheticSpread` 体现在盘口中，不是
`order_target*` 的额外滑点参数。

## 持仓、资金和挂单

接口签名和返回字段依据
[Backtest 接口说明](https://docs.dolphindb.com/zh/plugins/backtest/interface_description.html)。

```dos
allPositions = Backtest::getPosition(context.engine)
position = Backtest::getPosition(context.engine, stockCode, "stock")
cash = Backtest::getAvailableCash(context.engine, "stock")
portfolios = Backtest::getTotalPortfolios(context.engine)
openOrders = Backtest::getOpenOrders(context.engine)
```

不传 symbol 时 `getPosition` 返回表；指定 symbol 时返回字典。Arena 股票回测使用的持仓结构为：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `symbol` | STRING/SYMBOL | `.XSHG/.XSHE` 标准代码 |
| `lastDayLongPosition` | LONG | 昨日多头持仓 |
| `lastDayShortPosition` | LONG | 昨日空头持仓 |
| `longPosition` | LONG | 当前多头持仓 |
| `shortPosition` | LONG | 当前空头持仓 |
| `longPositionAvgPrice` | DOUBLE | 当前多头成交均价，执行价格尺度 |
| `shortPositionAvgPrice` | DOUBLE | 当前空头成交均价，执行价格尺度 |
| `todayBuyVolume` | LONG | 当日买入成交数量 |
| `todaySellVolume` | LONG | 当日卖出成交数量 |
| `totalValue` | DOUBLE | 当前持仓市值 |

组合总权益从 `portfolios["totalEquity"]` 读取；可用现金不等于总权益。不存在的代码或空持仓可能返
回空/NULL，使用 `.sum()` 后也应 `nullFill(..., 0)`。

`getOpenOrders` 返回挂单表或由字典组成的 tuple。非债券订单的完整结构为：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `orderId` | LONG | 订单 ID |
| `timestamp` | TIMESTAMP | 委托时间 |
| `symbol` | STRING | 证券代码 |
| `price` | DOUBLE | 委托限价 |
| `totalQty` | LONG | 原始委托数量 |
| `openQty` | LONG | 尚未成交数量 |
| `direction` | INT | 1 买开、2 卖开、3 卖平、4 买平 |
| `isMacthing` | INT | 是否已到达撮合时间；插件字段名即如此拼写 |
| `openVolumeWithBetterPrice` | LONG | 更优价未成交量，仅输出队列位置时存在 |
| `openVolumeWithWorsePrice` | LONG | 更差价未成交量，仅输出队列位置时存在 |
| `openVolumeAtOrderPrice` | LONG | 同价未成交量，仅输出队列位置时存在 |
| `priorOpenVolumeAtOrderPrice` | LONG | 同价且更早的未成交量，仅输出队列位置时存在 |
| `depthVolumeWithBetterPrice` | INT | 更优价档位深度，仅输出队列位置时存在 |
| `updateTime` | TIMESTAMP | 最新更新时间 |

读取前先判断返回对象类型和是否为空，不要假定固定为非空表。

撤单：

```dos
Backtest::cancelOrder(context.engine)
Backtest::cancelOrder(context.engine, stockCode)
Backtest::cancelOrder(context.engine, , orderIds)
Backtest::cancelOrder(context.engine, , , "orderLabel")
```

## `onOrder` 事件

插件通用事件结构见
[Backtest 插件文档](https://docs.dolphindb.com/zh/plugins/backtest.html)；下表是 Arena 当前
`msgAsTable=true`、未启用额外序号字段时实际收到的位置型 ANY VECTOR。

当前创建方式下 `orders` 是 ANY VECTOR，必须按整数位置读取，不能写 `orders["status"]`。

| 位置 | 字段 | 说明 |
| ---: | --- | --- |
| 0 | `orderId` | LONG 订单号 |
| 1 | `symbol` | STRING 证券代码 |
| 2 | `timestamp` | TIMESTAMP 委托时间 |
| 3 | `qty` | LONG 委托数量 |
| 4 | `price` | DOUBLE 委托价 |
| 5 | `status` | INT 状态 |
| 6 | `direction` | INT 方向 |
| 7 | `tradeQty` | LONG 累计成交量 |
| 8 | `tradeValue` | DOUBLE 累计成交额 |
| 9 | `label` | STRING 标签 |
| 10 | `updateTime` | TIMESTAMP 更新时间 |

完整 status：

| 值 | 状态 | 是否终态 | 调用方处理 |
| ---: | --- | --- | --- |
| `4` | 已报 | 否 | 记录 orderId，等待成交/撤单 |
| `0` | 部分成交 | 否 | 使用累计 `tradeQty`，剩余数量仍挂起 |
| `1` | 全部成交 | 是 | 清除 pending 状态 |
| `2` | 撤单成功 | 是 | 清除 pending 状态 |
| `-1` | 审批/风控拒绝 | 是 | 记录原因并计入拒单 |
| `-2` | 撤单拒绝 | 否 | 原订单可能仍活动，重新查询 `getOpenOrders` |
| `-3` | 回测结束仍未成交 | 是 | 计入期末未成交，不能当成已撤单 |

```dos
def onOrder(mutable context, orders) {
    orderId = long(orders[0])
    status = int(orders[5])
    context["orderStates"][orderId] = status
    if (status in [-1, -2, -3]) {
        context["rejectedOrders"] = context["rejectedOrders"] + 1l
    }
}
```

标准处理顺序是：提交后保存 orderId；`status in [4,0]` 时禁止对同一代码重复下目标；每日开始撤销
旧挂单；收到 `2`/`1`/`-1`/`-3` 后清理 pending；收到 `-2` 时以 `getOpenOrders` 的真实结果为准。
部分成交的持仓均价和止损只能在 `onTrade` 后按真实持仓更新，不能在 submitOrder 返回时设置。

## `onTrade` 事件

插件通用事件结构见
[Backtest 插件文档](https://docs.dolphindb.com/zh/plugins/backtest.html)；下表是 Arena 当前配置下
实际收到的位置型 ANY VECTOR。

`trades` 同样是 ANY VECTOR：

| 位置 | 字段 | 说明 |
| ---: | --- | --- |
| 0 | `orderId` | LONG 订单号 |
| 1 | `symbol` | STRING 证券代码 |
| 2 | `tradePrice` | DOUBLE 本次成交价 |
| 3 | `tradeQty` | LONG 本次成交量 |
| 4 | `tradeValue` | DOUBLE 本次成交额 |
| 5 | `totalFee` | DOUBLE 本次费用 |
| 6 | `totalVolume` | LONG 累计成交量 |
| 7 | `totalValue` | DOUBLE 累计成交额 |
| 8 | `direction` | INT 方向 |
| 9 | `tradeTime` | TIMESTAMP 成交时间 |
| 10 | `orderPrice` | DOUBLE 委托价 |
| 11 | `label` | STRING 标签 |

```dos
def onTrade(mutable context, trades) {
    context["filledShares"] = context["filledShares"] + long(trades[3])
    context["paidFees"] = context["paidFees"] + double(trades[5])
}
```

## 动态股票池退出

合成 message 来自第二阶段 filters 后的结果。如果某股票被第二阶段 filter 删除，它当天不在
message 中，`order_target` 无法对它下单。需要根据动态成员退出时：

1. `codes_query` 过滤成员，得到期间候选代码并集；
2. `dataset_query` 输出每日成员 BOOL derivative；
3. 通常保持第二阶段 `filters=[]`；
4. `getLastData(..., false)` 读取前一截面成员状态；
5. 对当前 message 中未入选但仍持有的股票下目标 0。

## 日频合成快照的能力边界

日线输入只生成开盘与收盘两个快照，事件路径中不存在盘中 high/low 发生的准确时刻。因此：

- 不能声称“盘中 low 触发止损”或“触及通道即成交”；
- 09:30 决策不能读取当日 low/high/close；
- 只能在开盘或收盘快照观察条件；下单后可与该时刻最新盘口即时撮合，未成交部分才等待后续快照；
- 跳空越过止损时，成交价由触发时可撮合盘口决定，不是止损价；
- 入场当日究竟先触及止损还是先触及盈利点，日线无法判定。

需要严格盘口内路径或盘中止损时，应使用真实分钟/快照数据和与之对应的 Runtime 模式；当前 MCP
BacktestParameters 固定的日线合成模式不能表达该精度。

## 证券代码对齐

请求中的 A 股代码使用 `510300.SH` / `000001.SZ`；查询完成后 Runtime 将回测表、message 和历史
helper 中的代码统一为 `510300.XSHG` / `000001.XSHE`。不要在回调里把 `.SH` 字符串与
`message.symbol` 混比，也不要二次手工转换。推荐始终从 `message.symbol[rowIndex]` 取得 SYMBOL，
用它筛选 `getLastData` 返回表；只有作为字典 key 或日志文本时再 `string(stockCode)`。

## 运行诊断

### 使用 DOS 输出调试

回测 Session 的 DolphinDB 消息输出已由 Runtime 从默认 stdout 重定向到 Loguru，Loguru 输出随后进入
DolphinScheduler Task 日志。因此 `utils` 和八个 callback 中的 `print(...)` 可以直接用于调试，无需
另建日志文件。例如可打印当前回调日期、筛选后行数、提交的 orderId 以及 `onOrder`/`onTrade` 收到的
状态摘要：

```dos
print("调仓日期=" + string(date(message.timestamp[0])) +
      ", 候选数量=" + string(selected.rows()))
```

运行后先用 `list_workflow_tasks(workflow_instance_id)` 找到 `task_instance_id`，再从
`get_task_logs(..., skip_line_num=0)` 开始分页读取，直到 `has_more=false`；也可以用
`get_task_log_download` 下载完整日志。DolphinDB 的语法错误和运行异常同样会出现在该 Task 日志中。

调试输出应是少量摘要。不要打印密码、Token、完整行情表或每只股票每个快照的全部数据；大量输出会
拖慢回测并放大日志。`print` 只能证明代码路径与当时变量值，订单是否真正成交仍必须以 `onOrder`、
`onTrade`、`trade_details` 和持仓结果为准。

回测 SUCCESS 但无交易时依次检查：

- `onSnapshot` 的时间条件是否匹配 09:30 或 15:00；
- `getLastData` 是否为空；
- 第二阶段是否把目标股票从 message 删除；
- 目标市值换算后是否小于 100 股；
- 是否存在未撤销挂单；
- `orders[5]`、`trade_details` 是否显示拒单或未成交；
- 现金、可卖持仓、涨跌停、限价、延时和 `syntheticSpread` 是否允许当前或后续盘口成交。

## SUCCESS 后的结果 QA

工作流成功后至少检查以下内容；任一项失败都不能直接采信收益指标：

- 随机抽查交易，确认信号行日期严格早于下单回调日期，窗口没有多 shift 或少 shift；
- 重算 `prevClosePrice / raw close`，核对价格型信号、ATR、止损距离和仓位分母处于同一尺度；
- 按 orderId 对账委托量、累计成交量和最终状态，部分成交不能算全成；
- 分别统计 `-1` 拒单、`-2` 撤单拒绝和 `-3` 期末未成交，并检查重复挂单；
- 用 `trade_details` 与 `daily_positions` 对账持仓，不允许非法负多头；
- 检查 `daily_portfolios` 日期递增，净值、权益和现金有限非空，费用增量符合配置；
- 检查关键信号 NULL 比例、每日可交易代码数和缺失交易日是否异常；
- 明确期末持仓处理；Arena 不会为了报表自动平仓。

## 指标口径

- 日收益：相邻有效 `netValue` 的变化率；
- 年化收益：最后有效净值按有效净值行数和 `annual_trading_days` 几何年化；
- 年化波动：日收益总体标准差乘 `sqrt(annual_trading_days)`；
- Sharpe：`(annualReturn - risk_free_rate) / annualVolatility`；
- 费用：插件交易费用，受 commission、tax、最低单笔费用及买卖方向影响；
- 期末持仓：不自动卖出，最后净值包含未平仓市值；
- 基准：当前请求没有基准参数，不自动扣除基准收益或基准成本。

后端展示的胜率是非零日收益中正收益日的比例，不是逐笔交易胜率；最大回撤从初始净值 1.0 开始
计算。比较结果时必须固定年化日数、无风险利率、费用、复权方式和期末持仓处理。
