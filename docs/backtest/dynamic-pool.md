# 动态指数股票池与防未来泄漏

本例完整展示两阶段动态指数股票池。第一阶段只取得整个回测区间内曾经入选的代码并集；第二阶段
重新计算逐日成员状态，并故意保留 `filters=[]`。回调在 t 日 09:30 只读取 t 日以前最后一个完整
截面，用该截面的成员状态和排名决策。这样既不把未来成分提前视为成员，也不会让调出指数的旧持仓
从 message 中消失。

## 必须同时满足的四条规则

1. `codes_query.filters=["stock_pool_member"]`：只用于得到期间候选代码并集；
2. `dataset_query` 再定义同名逐日 `stock_pool_member`：它才是每日成员事实；
3. 截面算符用 `on="stock_pool_member"`，但 `dataset_query.filters=[]`：非成员行仍留在数据和 message
   中，已有持仓可以下目标 0；
4. t 日回调使用 `backtest::getLastData(context, message, false)`：返回严格早于 t 日的最后完整截面。

只做第 1 条、然后把候选并集全部当成每天的成员，会产生未来成分泄漏。把每日成员放进第二阶段
filters，则调出成分和必要价格缺失的证券可能无法在当日 message 中退出。

## 可直接提交的完整请求

先创建 Backtest 项目，然后把下面整个对象作为 `run_backtest` 的 `parameters`。日期、指数、排名阈值
可以修改，但不能删除第二阶段成员门控或把它放进 filters。

```json
{
  "config": {
    "cash": 1000000,
    "commission": 0.0003,
    "tax": 0.001,
    "syntheticSpread": 0.001,
    "enableMinimumPerTransactionFee": true,
    "enableSellCloseRestrict": true,
    "outputOrderInfo": true
  },
  "params": {
    "rebalanceDays": 20,
    "maxPositions": 10,
    "capitalRatio": 0.9,
    "rankFloor": 0.8
  },
  "codes_query": {
    "start_date": "2020-01-01",
    "end_date": "2025-12-31",
    "lookback": "PT0S",
    "codes": [],
    "factors": [],
    "derivatives": {
      "stock_pool_member": {
        "type": "DIRECT",
        "op": "binary.gt",
        "fields": {"left": "weight_000300SH", "right": 0},
        "params": {}
      }
    },
    "filters": ["stock_pool_member"]
  },
  "dataset_query": {
    "start_date": "2020-01-01",
    "end_date": "2025-12-31",
    "lookback": "P120D",
    "codes": [],
    "factors": [],
    "derivatives": {
      "stock_pool_member": {
        "type": "DIRECT",
        "op": "binary.gt",
        "fields": {"left": "weight_000300SH", "right": 0},
        "params": {}
      },
      "return_1d": {
        "type": "TS",
        "op": "unary.pct_change",
        "fields": {"col": "close"},
        "params": {"periods": 1}
      },
      "momentum_60d": {
        "type": "TS",
        "op": "unary.pct_change",
        "fields": {"col": "close"},
        "params": {"periods": 60}
      },
      "volatility_20d": {
        "type": "TS",
        "op": "unary.rolling_std",
        "fields": {"col": "return_1d"},
        "params": {"window": 20, "min_periods": 20}
      },
      "member_momentum_rank": {
        "type": "CS",
        "op": "unary.rank_pct",
        "fields": {"col": "momentum_60d"},
        "params": {"ascending": true, "ties_method": "average"},
        "on": "stock_pool_member"
      }
    },
    "filters": []
  },
  "adj": "qfq",
  "annual_trading_days": 250,
  "risk_free_rate": 0.03,
  "utils": "def selectedDynamicCodes(signal, maxPositions, rankFloor) {\n    eligible = select code, member_momentum_rank from signal where stock_pool_member == true, momentum_60d > 0, volatility_20d > 0, member_momentum_rank >= rankFloor\n    selectedCount = min(long(maxPositions), eligible.rows())\n    if (selectedCount == 0) return symbol(take(\"\", 0))\n    selectedIndexes = isort(eligible.member_momentum_rank, false)[0:selectedCount]\n    return symbol(string(eligible.code[selectedIndexes]))\n}\n\ndef rebalanceDynamicPool(mutable context, message, signal) {\n    selectedCodes = selectedDynamicCodes(signal, context[\"maxPositions\"], context[\"rankFloor\"])\n    portfolio = Backtest::getTotalPortfolios(context.engine)\n    totalEquity = double(portfolio[\"totalEquity\"])\n    targetValue = iif(size(selectedCodes) == 0, 0.0, totalEquity * context[\"capitalRatio\"] / size(selectedCodes))\n    for (rowIndex in 0..(message.rows() - 1)) {\n        stockCode = message.symbol[rowIndex]\n        if (!(stockCode in selectedCodes)) backtest::order_target(context, message, stockCode, 0l, \"memberExit\")\n    }\n    for (rowIndex in 0..(message.rows() - 1)) {\n        stockCode = message.symbol[rowIndex]\n        if (stockCode in selectedCodes) backtest::order_target_value(context, message, stockCode, targetValue, \"memberEntry\")\n    }\n}",
  "callbacks": {
    "initialize": "def initialize(mutable context) { params = getParams(); context[\"tradingDays\"] = 0l; context[\"rebalanceDays\"] = long(params[\"rebalanceDays\"]); context[\"maxPositions\"] = long(params[\"maxPositions\"]); context[\"capitalRatio\"] = double(params[\"capitalRatio\"]); context[\"rankFloor\"] = double(params[\"rankFloor\"]); context[\"orderEvents\"] = 0l; context[\"tradeEvents\"] = 0l }",
    "beforeTrading": "def beforeTrading(mutable context) { Backtest::cancelOrder(context.engine); return NULL }",
    "onBar": "def onBar(mutable context, message, indicator) { return NULL }",
    "onSnapshot": "def onSnapshot(mutable context, message, indicator) { if (message.rows() == 0 || time(message.timestamp[0]) != 09:30:00.000) return; context[\"tradingDays\"] = context[\"tradingDays\"] + 1l; if ((context[\"tradingDays\"] - 1l) % context[\"rebalanceDays\"] != 0l) return; signal = backtest::getLastData(context, message, false); if (signal.rows() == 0) return; rebalanceDynamicPool(context, message, signal) }",
    "onOrder": "def onOrder(mutable context, orders) { context[\"orderEvents\"] = context[\"orderEvents\"] + 1l }",
    "onTrade": "def onTrade(mutable context, trades) { context[\"tradeEvents\"] = context[\"tradeEvents\"] + 1l }",
    "afterTrading": "def afterTrading(mutable context) { return NULL }",
    "finalize": "def finalize(mutable context) { print(\"动态股票池回测完成：交易日=\" + string(context[\"tradingDays\"]) + \"，订单事件=\" + string(context[\"orderEvents\"]) + \"，成交事件=\" + string(context[\"tradeEvents\"])) }"
  }
}
```

## 执行时间线

```text
全区间 codes_query
  -> 对过滤结果 code 去重，形成“期间曾经入选”的候选并集
  -> dataset_query 为候选并集计算每个交易日的 stock_pool_member 和排名
  -> t 日 09:30 onSnapshot
  -> getLastData(..., false) 取得严格早于 t 的最后完整截面
  -> 只选择该截面 member=true 的证券
  -> 先对未入选的 message 证券下目标 0，再提交目标市值买单
```

`codes_query` 的结果并集可能包含区间后期才加入指数的证券。它们的历史数据被加载不等于它们提前成为
成员；真正的 point-in-time 门控发生在第二阶段每日 BOOL、截面 `on` 和回调选择条件中。指数权重和
财务基础数据仍可能被供应商回溯修订，Arena 不保存数据快照；严格复现必须固定基础数据版本。

## 预期结果与 QA

请求校验、DolphinDB 编译和工作流成功后，应列出四个默认输出：`trade_details`、
`daily_positions`、`daily_portfolios`、`daily_trading_statistics`。不能预先承诺交易笔数或收益。

2026-08-15 使用本文原样请求的验证快照为：第一阶段候选并集 481 只，`trade_details` 2148 行、
`daily_positions` 14705 行、`daily_portfolios` 1455 行、`daily_trading_statistics` 1063 行，Runtime
回测完整结束。基础数据会更新，这些行数只用于证明示例完成了真实查询、撮合和结果读取，不是固定断言。
至少验证：

- t 日选股所用 signal.time 严格小于 t；
- 每日入选代码的 `stock_pool_member=true`，而候选并集中的非成员没有被选入；
- 调出成员后，只要当日仍有有效快照，存在目标 0 的卖出订单或已无持仓；
- 期末仍持有但已不满足成员的证券，应结合停牌/缺价检查是否无法退出；
- `qfq` 只改变执行 message 的价格尺度；本例信号是无量纲收益和波动率，没有把原始价格水平直接与
  复权持仓价混算。

完整字段、撮合和 QA 见 `arena://docs/backtest/dolphindb` 与 `arena://docs/backtest/qa`。本文最后按
DolphinDB Server `2.00.18`、Backtest `2.00.18.11`、MatchingEngineSimulator `2.00.18.11` 于
2026-08-15 完成 Runtime Schema 与 DolphinDB 编译验证。
