# Arena 的 DolphinDB Backtest 适配契约

本文只定义在 Arena `run_backtest` 中可依赖的插件输入、Runtime 注入对象和交易接口。通用
内置函数签名由 `describe_dolphindb_functions` 从当前服务器 `defs()` 实时读取。

官方依据：

- Backtest 接口：<https://docs.dolphindb.com/zh/plugins/backtest/interface_description.html>
- 股票回测配置和数据结构：<https://docs.dolphindb.com/zh/plugins/backtest/stock.html>
- Backtest 快速上手：<https://docs.dolphindb.com/zh/plugins/backtest/quick_start.html>

官方文档描述插件的全部模式；下文进一步收窄为 Arena 当前日频股票适配。

## 1. 引擎固定边界

Runtime 使用 `Backtest::createBacktestEngine` 创建股票引擎，并固定：

| 配置 | 值 | 业务结果 |
| --- | --- | --- |
| `strategyGroup` | 0 | 股票账户 |
| `dataType` | 4 | 日频 |
| `msgAsTable` | true | `onBar` 的 message 是表 |
| `matchingMode` | 2 | 日频订单按当前行情开盘价撮合 |
| `startDate`、`endDate` | dataset 查询区间 | 与回测数据一致 |

这些 key 由 Runtime 注入，用户 `config` 出现任意一个都会在提交前校验失败。尤其
`matchingMode` 不允许自定义。

Runtime 加载 `MatchingEngineSimulator`、`Backtest` 插件和 `backtest` 模块，注册请求中的八个
回调，追加整张日频 message，最后追加 END marker。引擎或回调异常时销毁引擎并把异常向上抛出。

## 2. Runtime 生成的日频 message

数据源是 `dataset_query` 的 filters 前结果。Runtime 自动确保读取以下 CoreData 行情列：
`open`、`low`、`high`、`close`、`vol`、`up_limit`、`down_limit`、`pre_close`；复权时另读
`adj_factor`。

输出给插件的 message 列：

| 列 | 类型 | Runtime 转换 |
| --- | --- | --- |
| `symbol` | SYMBOL | `000001.SZ -> 000001.XSHE`；`600000.SH -> 600000.XSHG` |
| `tradeTime` | TIMESTAMP | 数据日期加 15:00 |
| `open`、`low`、`high`、`close` | DOUBLE | 按 `adj` 选择不复权、hfq 或 qfq |
| `volume` | LONG | CoreData `vol` 由手乘 100 转为股 |
| `upLimitPrice` | DOUBLE | 使用 up_limit；缺失时由昨收 10% 与当日 high 保护计算 |
| `downLimitPrice` | DOUBLE | 使用 down_limit；缺失时由昨收 -10% 与当日 low 保护计算 |
| `prevClosePrice` | DOUBLE | 来自 pre_close，并按相同规则复权 |

任一必需行情值仍为 NULL 的行会被删除。message 按 `tradeTime,symbol` 排序。DSL 自定义列不会
附加到 message，`message.momentum` 这类访问是错误的。

`onBar(context,message,indicator)` 每个交易日接收当日可用股票组成的表。常用字段访问：

```dos
rowCount = message.rows()
symbols = message.symbol
prices = double(message.open)
executionDate = date(message.tradeTime[0])
```

`indicator` 是插件订阅指标参数；Arena 没有把 Factor Query derivative 订阅成插件 indicator，
策略因子必须从下一节的 Runtime 数据接口读取。

## 3. DSL 数据与无未来数据接口

Runtime 在引擎 context 中注入：

| key | 内容 |
| --- | --- |
| `coreBacktestUnfilteredFactorData` | dataset filters 前完整 DSL 表 |
| `coreBacktestFilteredFactorData` | dataset filters 后 DSL 表 |

策略正常情况下不要直接查询这两张完整表，因为它们同时包含回测未来日期。使用 Runtime 模块：

```dos
lastSignal = backtest::getLastData(context, message, true)
fullHistory = backtest::getHistoryData(context, message, true)
```

第三个参数：

- `true`：读取 filters 后表；
- `false`：读取 filters 前表。

两函数都以 `date(message.tradeTime[0])` 为边界，只返回严格早于当前消息日期的数据。
`getLastData` 返回其中最后一个实际存在的时间截面，而不是简单减一天，因此可跨周末、节假日和
停牌空档。

下列行为构成未来数据泄漏：

- 在当日开盘撮合前使用当日收盘计算的 derivative；
- 直接从注入的完整表查询 `date(time) >= currentDate`；
- 把负 shift 的未来收益列用于 onBar 信号。

## 4. context 和策略参数

插件在 context 提供至少 `engine` 和当前 `tradeTime`。Runtime 额外注入上述两张 DSL 表。
用户状态必须写到 mutable context：

```dos
def initialize(mutable context) {
    p = getParams()
    context["barCount"] = 0l
    context["rebalanceBars"] = long(p["rebalanceBars"])
}
```

`getParams()` 返回请求 `parameters.params` 上传到本次 DolphinDB session 的字典。应在
`initialize` 读取并显式转成策略需要的类型；不要在 callbacks 中硬编码原本应该参与敏感性分析
的值。

`utils` 在 callbacks 之前原样执行，可声明多个复用函数，也可包含确实需要的全局初始化语句。
所有需要在回调中调用的函数都必须在同一 `utils` 中定义，不能依赖另一个请求或 session 的
全局对象。

## 5. 八个回调

| 回调 | 触发 | Arena 中的职责 |
| --- | --- | --- |
| `initialize(context)` | 引擎启动一次 | 读取 params，初始化 context |
| `beforeTrading(context)` | 每交易日前 | 重置日内状态、检查挂单 |
| `onBar(context,message,indicator)` | 每个日频截面 | 读取历史信号、计算目标、下单 |
| `onSnapshot(context,message,indicator)` | 当前日频路径通常不用 | 必须定义，可返回 NULL |
| `onOrder(context,events)` | 委托状态变化 | 拒单、撤单、部分成交状态处理 |
| `onTrade(context,events)` | 成交发生 | 记录真实成交和费用 |
| `afterTrading(context)` | 每日盘后 | 日度状态收尾 |
| `finalize(context)` | END marker 后 | 最终日志和清理 |

JSON 中 callbacks 必须正好包含这些名称；每个值必须是同名完整 `def`，参数数量固定。Runtime
在创建 Workspace 前已对 utils 和回调进行真实 DolphinDB 编译预检。

## 6. onOrder 事件

当前 Arena 部署的插件把第二个参数传为 `ANY VECTOR`，每个元素是一条
`STRING->ANY DICTIONARY`。必须先遍历，再读取单条事件：

```dos
def onOrder(mutable context, events) {
    for (event in events) {
        orderId = long(event["orderId"])
        status = int(event["status"])
        symbolValue = string(event["symbol"])
    }
}
```

已验证的字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `orderId` | LONG | 委托 ID |
| `symbol` | STRING | 插件代码 `.XSHE/.XSHG` |
| `timestamp` | TIMESTAMP | 委托时间 |
| `qty` | LONG | 委托数量 |
| `price` | DOUBLE | 委托价格 |
| `status` | INT | 4 已报，0 部成，1 已成，2 撤单成功，负值为拒绝 |
| `direction` | INT | 1 买开，2 卖开，3 卖平，4 买平 |
| `tradeQty` | LONG | 累计成交/状态数量 |
| `tradeValue` | DOUBLE | 累计成交金额 |
| `label` | STRING | submitOrder 标签 |
| `updateTime` | TIMESTAMP | 状态更新时间 |
| `seqNum` | LONG | 启用对应输出时的订单序号 |
| `tradeSeqNum` | LONG | 启用对应输出时的成交序号 |

不要写 `events["status"]`；外层是向量，不是字典。状态处理不能代替持仓查询：订单可能部分
成交、拒绝或撤单。

## 7. onTrade 事件

第二个参数同样是 `ANY VECTOR`：

```dos
def onTrade(mutable context, events) {
    for (event in events) {
        quantity = long(event["tradeQty"])
        price = double(event["tradePrice"])
        fee = double(event["totalFee"])
    }
}
```

已验证字段：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `orderId` | LONG | 所属委托 ID |
| `symbol` | STRING | 证券代码 |
| `tradePrice` | DOUBLE | 本次成交价 |
| `tradeQty` | LONG | 本次成交数量 |
| `tradeValue` | DOUBLE | 本次成交金额 |
| `totalFee` | DOUBLE | 本次成交费用 |
| `totalVolume` | LONG | 该订单累计成交数量 |
| `totalValue` | DOUBLE | 该订单累计成交金额 |
| `direction` | INT | 买卖方向 |
| `tradeTime` | TIMESTAMP | 成交时间 |
| `orderPrice` | DOUBLE | 原委托价格 |
| `label` | STRING | 用户标签 |
| `seqNum`、`tradeSeqNum` | LONG | 启用对应输出时的序号 |

需要成交驱动状态时用 onTrade；普通定期再平衡仍应在下一次运行时重新查询引擎真实持仓。

## 8. 股票普通订单

Arena 使用 `submitOrder` 的普通订单模式（外层 `orderType` 保持默认 0）：

```dos
orderId = Backtest::submitOrder(
    context.engine,
    (symbolValue, context.tradeTime, priceType, price, quantity, direction),
    label
)
```

六项元组严格为：

| 位置 | 含义 | Arena 日频常用值 |
| --- | --- | --- |
| 1 | 股票代码 | message 中的 `.XSHE/.XSHG` SYMBOL |
| 2 | 下单时间 | `context.tradeTime` |
| 3 | 交易所订单类型 | 5 为限价单 |
| 4 | 委托价格 | 通常为当前 `message.open[index]` |
| 5 | 委托数量 | 正的 LONG 股数，不是金额 |
| 6 | 方向 | 1 买开；3 卖平现有多头 |

固定 `matchingMode=2` 的日频订单按开盘价撮合，但仍可能因停牌、涨跌停、数量、现金或风控被
拒绝/未成交。金额必须先换算并按市场交易单位向下取整。卖出数量不得超过实际多头可用数量。

数据代码与插件代码转换：

```dos
def toDataCode(symbolValue) {
    return strReplace(
        strReplace(string(symbolValue), ".XSHE", ".SZ"),
        ".XSHG",
        ".SH"
    )
}
```

## 9. 持仓、现金与权益

当前股票账户可使用：

```dos
allPositions = Backtest::getPosition(context.engine)
onePosition = Backtest::getPosition(context.engine, symbolValue, "stock")
cash = Backtest::getAvailableCash(context.engine, "stock")
portfolio = Backtest::getTotalPortfolios(context.engine)
```

`getPosition` 不传 symbol 返回表；传 symbol 的股票重载可读取 `longPosition` 等字段。安全读取：

```dos
values = Backtest::getPosition(context.engine, symbolValue, "stock")["longPosition"]
quantity = iif(count(values) == 0 || isNull(values[0]), 0l, long(values[0]))
```

股票持仓关键字段：`symbol`、`lastDayLongPosition`、`longPosition`、
`longPositionAvgPrice`、`todayBuyVolume`、`todaySellVolume`、`totalValue`。

权益关键字段：`tradeDate`、`cash`、`totalMarketValue`、`totalEquity`、`netValue`、
`totalReturn`、`ratio`、`pnl`、`frozenFunds`、`totalFee`、`floatingPnl`、
`realizedPnl`、`totalPnl`。组合权重通常应基于 `totalEquity`，不能把可用现金误当总资产。

## 10. 未成交订单与撤单

官方接口：

```dos
allOpen = Backtest::getOpenOrders(context.engine)
symbolOpen = Backtest::getOpenOrders(context.engine, symbolValue)
idsOpen = Backtest::getOpenOrders(context.engine, , orderIds)
labelOpen = Backtest::getOpenOrders(context.engine, , , "rebalanceBuy")

Backtest::cancelOrder(context.engine, symbolValue)
Backtest::cancelOrder(context.engine, , orderIds)
Backtest::cancelOrder(context.engine, , , "rebalanceBuy")
Backtest::cancelOrder(context.engine)
```

重复调仓前必须决定如何处理已有挂单：继续保留、按 symbol 撤销或按 label 撤销。若忽略挂单，
新旧订单会叠加，目标仓位和现金约束都会失真。

## 11. 一个完整再平衡周期

1. `getLastData`/`getHistoryData` 读取严格早于当前日的数据；
2. 计算信号、目标股票和权重；
3. 查询现有挂单并按策略规则处理；
4. 用 `getPosition` 和 `getTotalPortfolios` 获取真实仓位、总权益；
5. 将目标金额换成合法目标股数；
6. 计算 `target - current`；
7. 先提交卖单，再依据资金约束提交买单；
8. 在 onOrder/onTrade 记录拒单和成交；
9. 下一次调仓重新读取引擎状态，不假设订单全部成交。

信号选择、风险模型、仓位优化、止损/退出和调仓频率属于策略本身，Arena 不替用户生成，也不
应被压缩成单行 callback。

## 12. Arena 结果

| MCP 输出名 | 插件接口 | 关键字段 |
| --- | --- | --- |
| `trade_details` | `Backtest::getTradeDetails` | orderId、symbol、direction、sendTime、orderPrice、orderQty、tradeTime、tradePrice、tradeQty、orderStatus、label |
| `daily_positions` | `Backtest::getDailyPosition` | tradeDate、symbol、longPosition、均价、当日买卖量 |
| `daily_portfolios` | `Backtest::getDailyTotalPortfolios` | cash、totalMarketValue、totalEquity、netValue、totalReturn、ratio、费用和盈亏 |
| `daily_trading_statistics` | `Backtest::getDailyTradingStatistics` | 每日各方向成交数量、金额、均价 |
| `return_summary` | `Backtest::getReturnSummary` + Runtime 标准化 | totalReturn、annualReturn、annualVolatility、sharpeRatio、maxDrawdown 等 |
| `engine_stat` | `Backtest::getBacktestEngineStat` | status、lastErrMsg、snapshotTimestamp |

普通 Backtest 项目默认请求前四项。遇到运行失败，先读 Workspace 的完整根异常；需要上下文时
分页读取 Task 日志。遇到回测成功但交易异常，再结合 `trade_details.orderStatus`、持仓、现金和
策略事件日志判断原因。

## 13. 必须避免的错误

- 把金额直接放到订单数量位置；
- 用 `.SZ/.SH` 代码调用插件订单接口；
- 使用当日收盘因子在当日开盘成交；
- 把 DSL derivative 当成 message 列；
- dataset filters 删除了需要卖出的原持仓股票；
- 使用 context 自建数量替代 `getPosition`；
- 不检查挂单就重复下单；
- 卖出超过持仓，或买入超过现金；
- 覆盖 `matchingMode`、`dataType`、`msgAsTable` 等 Runtime 固定配置；
- 猜测 DolphinDB 内置函数签名。对 `find` 等函数先调用实时签名工具。
