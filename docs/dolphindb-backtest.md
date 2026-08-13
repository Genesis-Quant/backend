# DolphinDB Backtest 运行契约

本页只描述 Arena Runtime 当前实际创建的股票回测环境，不是 DolphinDB 语言教程，也不覆盖插件
其它 dataType 或 matchingMode。策略可以依赖本文列出的 message、Runtime helper 和回调事件结构。

DolphinDB 普通内置函数的部署版本签名可用 `describe_dolphindb_functions` 查询。带
`Backtest::` 或 `backtest::` 命名空间的接口不要传给该工具，其契约以本页为准。

## 固定引擎配置

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

Runtime 从 `dataset_query` filters 后的表生成消息。每个有效交易日、每只代码生成两条快照：

| 时间 | `lastPrice` |
| --- | --- |
| `09:30:00` | 当日 `open` |
| `15:00:00` | 当日 `close` |

`adj=null` 不复权；`hfq` 使用 `adj_factor` 后复权；`qfq` 使用每只代码最后一个 `adj_factor` 归一
后前复权。`open/low/high/close/upLimitPrice/downLimitPrice/prevClosePrice` 使用相同调整系数。必要
价格为空的日线不会生成快照。

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

## 回放和撮合时点

每个交易日依次回放 09:30 和 15:00 快照。没有时间判断的 `onSnapshot` 每天执行两次。

引擎先处理当前行情，再调用该行情的 `onSnapshot`。因此在回调中提交的新订单不能反过来与已经
处理完的当前快照成交，最早等待后续可撮合快照。订单是否成交还取决于限价、涨跌停、可用资金、
可卖持仓、延时、费用和插件风控。`submitOrder` 返回订单号不表示成交；以 `onTrade` 和
`trade_details` 为准。

在 09:30 使用前一交易日信号是常见用法：

```dos
if (time(message.timestamp[0]) != 09:30:00.000) return
signal = backtest::getLastData(context, message, false)
```

这时新订单等待当日后续快照。若策略在 15:00 提交订单，则在当前日没有更晚合成快照。

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

```dos
allPositions = Backtest::getPosition(context.engine)
position = Backtest::getPosition(context.engine, stockCode, "stock")
cash = Backtest::getAvailableCash(context.engine, "stock")
portfolios = Backtest::getTotalPortfolios(context.engine)
openOrders = Backtest::getOpenOrders(context.engine)
```

持仓常用列包括 `symbol`、`longPosition`、`longPositionAvgPrice`、`lastDayLongPosition`、
`todayBuyVolume`、`todaySellVolume`、`totalValue`。组合总权益从 `portfolios["totalEquity"]` 读取；
可用现金不等于总权益。

撤单：

```dos
Backtest::cancelOrder(context.engine)
Backtest::cancelOrder(context.engine, stockCode)
Backtest::cancelOrder(context.engine, , orderIds)
Backtest::cancelOrder(context.engine, , , "orderLabel")
```

## `onOrder` 事件

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

已使用的 status：`4` 已报、`0` 部成、`1` 已成、`2` 撤单成功、`-1` 审批拒绝、`-2` 撤单拒绝。

```dos
def onOrder(mutable context, orders) {
    if (int(orders[5]) < 0) {
        context["rejectedOrders"] = context["rejectedOrders"] + 1l
    }
}
```

## `onTrade` 事件

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

## 运行诊断

回测 SUCCESS 但无交易时依次检查：

- `onSnapshot` 的时间条件是否匹配 09:30 或 15:00；
- `getLastData` 是否为空；
- 第二阶段是否把目标股票从 message 删除；
- 目标市值换算后是否小于 100 股；
- 是否存在未撤销挂单；
- `orders[5]`、`trade_details` 是否显示拒单或未成交；
- 现金、可卖持仓、涨跌停、限价和 `syntheticSpread` 是否允许后续快照成交。
