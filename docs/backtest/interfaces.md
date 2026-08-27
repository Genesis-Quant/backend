# Backtest 接口白名单与能力矩阵

本页定义 Arena 策略代码可以依赖的 `Backtest::` 接口、Runtime 独占接口，以及当前股票日频合成
快照模式明确不支持的接口。它不是 DolphinDB Backtest 全资产接口的转录。

当前结论绑定以下运行环境：

| 项目 | 值 |
| --- | --- |
| Backtest 插件 | `2.00.16.32` |
| 验证日期 | `2026-08-25` |
| Arena 资产与行情 | 股票、`dataType=1`、`matchingMode=1`、09:30/15:00 单档合成快照 |
| 引擎创建方式 | Runtime 使用的兼容引擎入口；返回 form 以本页实测为准 |

官方完整定义见
[DolphinDB Backtest 接口说明](https://docs.dolphindb.cn/zh/plugins/backtest/interface_description.html)。
官方文档覆盖更多资产、行情和引擎创建方式；只有本页列入“策略可用”的接口才是 Arena 当前策略
契约。部署插件升级后必须重新验证本页，而不能仅修改版本号。

## 使用规则

- 策略只使用 `context.engine`，不得创建、删除、停止、恢复或持久化引擎；
- 只有“策略可用”表中的接口可以写入 `utils` 或八个生命周期回调；
- 返回值是 TABLE、DICTIONARY 还是标量属于接口契约，不能把对象强制当作位置型 ANY VECTOR；
- 空持仓、空挂单和不存在的证券可能返回空表、空字典或 NULL，使用前先检查 form、行数或 keys；
- 设置接口不能生成行情，也不能绕过当日 message 缺失、资金、持仓、涨跌停和插件风控；
- `Backtest::submitOrder` 返回订单号只表示提交，终态与成交仍以回调和结果表为准。

## 策略可用接口

| 接口 | 当前签名 | 当前返回与用途 | 调用阶段和限制 |
| --- | --- | --- | --- |
| `submitOrder` | `(engine, msg, [label=""], [orderType=0], [accountType=""], [algoOrderParam])` | 订单号；提交普通单或插件支持的算法单 | 行情回调中使用当前 message 的证券和时间；返回不代表成交 |
| `cancelOrder` | `(engine, [symbol=""], [orders], [label=""], [accountType=""])` | 取消匹配条件的活动订单 | 可按证券、订单号、标签或全部撤单；撤单结果看 `onOrder` |
| `getPosition` | `(engine, [symbol=""], [accountType=""])` | 不指定证券返回 TABLE；指定证券返回 DICTIONARY | 读取真实持仓；不得根据已提交订单推断持仓 |
| `getOpenOrders` | `(engine, [symbol=""], [orders], [label=""], [outputQueuePosition=false], [accountType=""], [includeUnaccepted=true], [source=""])` | 当前股票模式非空结果实测为 TABLE | 空结果和其他插件模式先检查 form；字段见下文 |
| `getAvailableCash` | `(engine, [accountType=""], [count=1])` | 可用现金数值 | 可用现金不是总权益；多笔买单前重新读取 |
| `getTotalPortfolios` | `(engine, [accountType=""])` | 当前部署实测为一行 TABLE | 读取当前账户权益；按列名读取，不依赖官方其他创建方式的 DICTIONARY 形式 |
| `getTodayPnl` | `(engine, symbol, [accountType=""])` | 当前部署实测为一行 TABLE，至少包含证券、累计 PnL、当日 PnL | 仅股票；不得按官方其他版本的 DICTIONARY 形式硬编码 |
| `getTodayTradingStatistics` | `(engine, [symbol=""], [accountType=""])` | 不指定证券返回当日统计 TABLE；指定证券的 form 使用前自省 | 只统计已经成交的方向、量、额和均价 |
| `getLastPrice` | `(engine, [symbol=""], [accountType=""])` | 当前部署实测为 DICTIONARY | 公共调用名是 `getLastPrice`，不是导出符号 `getLastestPrices` |
| `setPosition` | `(engine, symbol, qty, orderPrice, [lastPrice], [assetType])` | 设置占用初始资金的初始持仓 | 只能在第一批行情插入前调用，通常只在 `initialize` 使用；股票不支持负数卖开 |
| `setUniverse` | `(engine, symbolList)` | 设置引擎标的池 | 不生成 message，不替代两阶段查询或每日成员门控，也不能让缺价证券可交易 |
| `subscribeIndicator` | `(engine, marketDataType, metrics, [contractType])` | 注册状态指标，结果传入行情回调的 `indicator` | Arena 只使用 `marketDataType="snapshot"`；必须在 `initialize` 注册 |
| `getIndicatorSchema` | `(engine, [marketDataType])` | 返回订阅指标的空表 Schema | 只描述指标列，不描述 message、订单或成交对象 |
| `setTradingVolumeDist` | `(engine, volume)` | 设置 VWAP 的分时成交量/权重表 | 仅用于经当前插件验证的 VWAP 算法单；普通订单和 `order_target*` 不需要调用 |

`order_target`、`order_target_value`、`getLastData`、`getHistoryData`、`getParams`、`getParam`、
`getTradeDates`、`getIndustry` 和 `factorPreprocess` 是 Arena Runtime 的 `backtest::` helper，不属于
插件导出。其完整契约见 `arena://docs/backtest/dolphindb`。

## 当前持仓、挂单和权益返回

不指定证券的 `getPosition` 返回表，稳定股票字段为：

```text
symbol, lastDayLongPosition, lastDayShortPosition,
longPosition, shortPosition, longPositionAvgPrice, shortPositionAvgPrice,
todayBuyVolume, todayBuyValue, todaySellVolume, todaySellValue
```

指定证券时返回以相同字段为 key 的字典。当前股票持仓对象不能假定存在 `totalValue`；计算组合权益应
使用 `getTotalPortfolios`，计算单证券当前市值时必须使用真实数量和同一价格尺度的当前行情价格。

当前 `getOpenOrders` 非空表的稳定字段为：

```text
orderId, timestamp, symbol, price, totalQty, openQty, direction, label
```

启用队列位置输出时插件可能追加 `isMacthing`、不同价格层未成交量和 `updateTime` 等列；这些列不是
Arena 当前日频合成快照的稳定策略契约。

当前 `getTotalPortfolios` 股票结构按列名读取：

```text
tradeDate, cash, totalMarketValue, totalEquity, netValue, totalReturn,
ratio, pnl, frozenFunds, totalFee, floatingPnl, realizedPnl, totalPnl
```

配置 `benchmark` 时，`getTotalPortfolios` / `getDailyTotalPortfolios` 还会返回
`benchmarkClosePrice` 和 `benchmarkNetValue`。这两列属于组合结果，不是 `message` 的字段；策略
需要读取当前基准行情时，应明确识别配置的基准代码，不能把组合结果列当成行情回调列。

## 指标订阅

指标必须在 `initialize` 注册。回测已经开始后再调用 `subscribeIndicator` 可能抛错，并可能留下部分
注册状态，因此不能把失败后重试当作正常流程。

```dos
def initialize(mutable context) {
    metrics = dict(STRING, ANY)
    metrics[`meanPrice] = <mavg(lastPrice, 20)>
    Backtest::subscribeIndicator(context.engine, "snapshot", metrics)
}

def onSnapshot(mutable context, message, indicator) {
    if (rows(indicator) > 0 && `meanPrice in columnNames(indicator)) {
        // 按 symbol/timestamp 对齐后使用 indicator.meanPrice
    }
}
```

`getIndicatorSchema(context.engine, "snapshot")` 返回 `symbol`、`timestamp` 以及已订阅指标列组成的空
表。它不能用于发现 message、持仓、订单或成交字段。

`setTradingVolumeDist` 的表包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `symbol` | STRING/SYMBOL | 证券代码 |
| `time` | SECOND | 分时点 |
| `value` | INT/DOUBLE | 成交量或权重 |

Arena 当前普通日频策略不需要该接口。只有明确使用 `orderType=11` 的 VWAP 路径并完成该插件版本的
输入、成交和结果 QA 后才能使用。

## Runtime 负责的接口

以下接口已验证可用或属于插件管理接口，但由 Arena Runtime 独占。策略代码不得调用：

| 类别 | 接口 | Arena 处理方式 |
| --- | --- | --- |
| 引擎生命周期 | `createBacktestEngine`、`createBacktester`、`dropBacktestEngine` | Runtime 创建并在结果关闭时销毁 |
| 行情回放 | `appendQuotationMsg`、`appendEndMarker`、`endWaitAPI` | Runtime 构造完整 message 并驱动回放 |
| 结果提取 | `getTradeDetails`、`getDailyPosition`、`getDailyTotalPortfolios`、`getDailyTradingStatistics` | Runtime 在结束后生成四张标准 Parquet |
| 派生/诊断 | `getReturnSummary`、`getBacktestEngineStat`、`getConfig`、`getContextDict`、`getEventCallbacks`、`getBacktestEngineList` | 不属于标准业务输出；按需用于 Runtime 内部计算或诊断 |
| 数据与输出配置 | `setSecurityReference`、`getSecurityReference`、`setStockDividend`、`setTradingOutput` | 由 Runtime 的基础数据和输出链路管理 |
| 执行控制 | `stopBacktestEngine`、`triggerDailySettlement`、`updateEventCallbacks` | 当前历史回测策略不得自行控制；后两项面向其他模式 |
| 兼容入口 | `getStockTotalPortfolios` | 与统一权益接口重叠，策略统一使用 `getTotalPortfolios` |

标准结果只包含四张 Parquet。`getReturnSummary` 与 Backend/浏览器根据 `daily_portfolios` 计算的指标
重复，`getBacktestEngineStat` 是运行诊断，二者都不是 Workspace 输出契约。

## 当前模式不支持或禁止的接口

| 类别 | 接口 | 原因 |
| --- | --- | --- |
| 双边报价 | `submitQuoteOrder`、`cancelQuotes`、`getQuoteTradeDetails`、`getOpenQuotes` | 当前股票普通订单引擎不是 FICC 报价引擎 |
| FICC/行情源查询 | `getDateTime`、`setSchedule`、`setTimer`、`getTargetOpenOrders`、`getBestPrice`、`getBestQty`、`getPnl`、`getLongPosition`、`getShortPosition`、`getAverageCost`、`getInventory`、`getTotalTransactionByCount`、`getTotalSnapshotByEndtime` | 当前引擎调用实测返回无效 FICC 引擎 |
| 行情订阅 | `subscribeQuote`、`unsubscribeQuote` | Arena 由 Runtime 提交完整合成 message；该接口面向其他资产或模式 |
| 两融 | `getMarginSecuPosition`、`getMarginTradingPosition`、`getSecuLendingPosition` | 当前 `strategyGroup="stock"`，不是两融账户 |
| 其他资产权益 | `getFuturesTotalPortfolios`、`getOptionTotalPortfolios` | 当前没有期货或期权账户 |
| 数字货币 | `getCryptocurrencyPosition`、`getCryptocurrencyTotalPortfolios` | 当前部署可能返回兼容结构，但语义不属于股票契约，禁止依赖 |
| 模拟模式 | `setBacktestMode`、`updatePosition` | 前者已废弃并由 config 决定；后者当前部署不支持股票历史回测 |
| 持久化 | `enableEnginePersistence`、`restoreFromSnapshot`、`extractSnapshotInfo`、`forceTriggerEngineSnapshot` | Workspace 已负责结果持久化；当前部署验证持久化曾导致连接终止，MCP 与策略均禁止调用 |

不得因为 `objs(true)` 中存在某个导出，就推断它适合当前股票策略。也不得使用数字货币兼容别名绕过
股票接口白名单。

## 导出符号与公共调用名

插件导出信息中的第一个名称不一定是 DolphinDB 脚本调用名。当前需要识别的映射为：

| 导出符号 | 公共调用名 |
| --- | --- |
| `backtestGetOpenOrders` | `Backtest::getOpenOrders` |
| `getOpenQuoteOrders` | `Backtest::getOpenQuotes` |
| `getLastestPrices` | `Backtest::getLastPrice` |
| `setSecurityReferenceData` | `Backtest::setSecurityReference` |
| `setRealTimeOutputTable` | `Backtest::setTradingOutput` |
| `genIndicatorColumns` | `Backtest::getIndicatorSchema` |
| `setSimulatorTradingMode` | `Backtest::setBacktestMode` |

策略只使用本页公开调用名。`describe_dolphindb_functions` 不解析 `Backtest::` 插件命名空间，不能用
它替代本页能力矩阵。

## 标准结果来源

| Workspace 输出名 | Runtime 调用 | 是否标准业务输出 |
| --- | --- | --- |
| `trade_details` | `Backtest::getTradeDetails(engine)` | 是 |
| `daily_positions` | `Backtest::getDailyPosition(engine)` | 是 |
| `daily_portfolios` | `Backtest::getDailyTotalPortfolios(engine)` | 是 |
| `daily_trading_statistics` | `Backtest::getDailyTradingStatistics(engine)` | 是 |
| `return_summary` | `Backtest::getReturnSummary(engine)` 后再标准化 | 否；指标从 `daily_portfolios` 统一计算 |
| `engine_stat` | `Backtest::getBacktestEngineStat(engine)` | 否；属于运行诊断 |

四张标准表的完整列、可选列、状态与对账规则见 `arena://docs/backtest/results`。
