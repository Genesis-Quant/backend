# 回调、持仓、订单与成交字段诊断

本页提供一个只交易 100 股、专门打印运行时结构的完整请求。DOS `print(...)` 已由 Runtime 重定向到
对应 DolphinScheduler Task 日志；它适合确认当前插件版本的真实类型和 key，不应在大股票池的每个
快照打印全表。

静态字段总表与生命周期见 `arena://docs/backtest/dolphindb`。本例显式关闭额外序号字段；启用额外
输出后，事件仍按字典 key 读取，但应通过 `event.keys()` 确认新增字段。

## 可直接提交的完整诊断请求

```json
{
  "config": {
    "cash": 100000,
    "commission": 0.0003,
    "tax": 0.001,
    "syntheticSpread": 0.001,
    "enableMinimumPerTransactionFee": true,
    "enableSellCloseRestrict": true,
    "outputOrderInfo": true,
    "outputSeqNum": false,
    "outputTradeSeqNum": false
  },
  "params": {"targetShares": 100},
  "codes_query": null,
  "dataset_query": {
    "start_date": "2024-01-02",
    "end_date": "2024-01-10",
    "lookback": "P1D",
    "codes": ["000001.SZ"],
    "factors": ["close"],
    "derivatives": {},
    "filters": []
  },
  "adj": null,
  "annual_trading_days": 250,
  "risk_free_rate": 0.03,
  "utils": "",
  "callbacks": {
    "initialize": "def initialize(mutable context) { params = getParams(); context[\"targetShares\"] = long(params[\"targetShares\"]); context[\"printedSchema\"] = false; context[\"submitted\"] = false; context[\"orderEvents\"] = 0l; context[\"tradeEvents\"] = 0l }",
    "beforeTrading": "def beforeTrading(mutable context) { return NULL }",
    "onBar": "def onBar(mutable context, message, indicator) { return NULL }",
    "onSnapshot": "def onSnapshot(mutable context, message, indicator) { if (message.rows() == 0) return; if (!context[\"printedSchema\"]) { print(\"message schema:\"); print(schema(message).colDefs); positions = Backtest::getPosition(context.engine); print(\"positions schema:\"); print(schema(positions).colDefs); position = Backtest::getPosition(context.engine, message.symbol[0], \"stock\"); print(\"single position keys:\"); print(position.keys()); context[\"printedSchema\"] = true }; if (time(message.timestamp[0]) == 09:30:00.000 && !context[\"submitted\"]) { orderId = backtest::order_target(context, message, message.symbol[0], context[\"targetShares\"], \"schemaProbe\"); print(\"submitted orderId=\" + string(orderId)); context[\"submitted\"] = true } }",
    "onOrder": "def onOrder(mutable context, orders) { context[\"orderEvents\"] = context[\"orderEvents\"] + long(size(orders)); for (event in orders) print(\"onOrder orderId=\" + string(event[`orderId]) + \", symbol=\" + string(event[`symbol]) + \", status=\" + string(event[`status]) + \", direction=\" + string(event[`direction]) + \", tradeQty=\" + string(event[`tradeQty])) }",
    "onTrade": "def onTrade(mutable context, trades) { context[\"tradeEvents\"] = context[\"tradeEvents\"] + long(size(trades)); for (event in trades) print(\"onTrade orderId=\" + string(event[`orderId]) + \", symbol=\" + string(event[`symbol]) + \", price=\" + string(event[`tradePrice]) + \", qty=\" + string(event[`tradeQty]) + \", fee=\" + string(event[`totalFee])) }",
    "afterTrading": "def afterTrading(mutable context) { return NULL }",
    "finalize": "def finalize(mutable context) { print(\"诊断完成：订单事件=\" + string(context[\"orderEvents\"]) + \"，成交事件=\" + string(context[\"tradeEvents\"])) }"
  }
}
```

## 实际对象契约

### `message`

`message` 是一张表，同一回调每行一只证券：

| 列 | 类型 | 大小写与读取 |
| --- | --- | --- |
| `symbol` | SYMBOL | `message.symbol[row]`，代码为 `.XSHG/.XSHE` |
| `symbolSource` | SYMBOL | `XSHG` 或 `XSHE` |
| `timestamp` | TIMESTAMP | 同一张表各行相同；当前只会是 09:30 或 15:00 |
| `lastPrice` | DOUBLE | 09:30 为 open，15:00 为 close，使用 `adj` 执行尺度 |
| `upLimitPrice` / `downLimitPrice` | DOUBLE | 执行尺度涨跌停边界 |
| `totalBidQty` / `totalOfferQty` | LONG | 当前合成盘口均为十亿 |
| `bidPrice` / `offerPrice` | DOUBLE ARRAY VECTOR | 一档按 `message.bidPrice[0][row]` 读取 |
| `bidQty` / `offerQty` | LONG ARRAY VECTOR | 一档按 `message.bidQty[0][row]` 读取 |
| `prevClosePrice` | DOUBLE | 同执行尺度前收盘 |

它不包含 `dataset_query` derivatives。历史信号必须调用 `getLastData` 或 `getHistoryData`。

### `getLastData`

返回一张普通表，列是第二阶段 Query 的 `time`、`code`、基础 factors 和 derivatives。`code` 已统一为
`.XSHG/.XSHE`。在 t 日回调中只包含 `date(time)<t` 的数据；`getLastData` 取最后一个实际存在的完整
时间截面，不会分别为每只股票寻找最后非空行。`filter=false` 读取第二阶段 filters 前的表，动态池
退出通常必须使用 false。

### `getPosition`

- `Backtest::getPosition(engine)` 返回表，可用 `schema(...).colDefs`；
- `Backtest::getPosition(engine, symbol, "stock")` 返回字典，可用 `.keys()`；
- 当前可依赖 `symbol`、昨日/当前多空数量、持仓均价和当日买卖数量；
- **不得假定存在 `totalValue`**。当前证券市值应以真实 `longPosition * message.lastPrice[row]` 计算；
  组合权益使用 `Backtest::getTotalPortfolios(engine)["totalEquity"]`；
- 空仓可能是空向量或 NULL，数量读取使用 `long(nullFill(position.longPosition.sum(), 0))`。

### `orders` 与 `trades`

当前两者都是 ANY VECTOR，**每个元素是 STRING->ANY DICTIONARY**。一次回调可能包含多个事件：

```text
orders[event] = {
  orderId, symbol, timestamp, qty, price, status, direction,
  tradeQty, tradeValue, label, updateTime
}

trades[event] = {
  orderId, symbol, tradePrice, tradeQty, tradeValue, totalFee,
  totalVolume, totalValue, direction, tradeTime, orderPrice, label
}
```

订单提交返回 `orderId` 不等于成交。对每个 `event in orders` 用 `event["status"]` 读取状态；对每个
`event in trades` 用 `event["tradeQty"]` 读取本次真实成交量。`orders[0]` 是第一个事件字典，不是订单
号；`orders[5]` 不是状态。不要假定持仓字典有 `totalValue`。

## A 股数量规则

- 普通股票买入目标按 100 股整数手；目标 0 可以精确清仓不足一手的剩余持仓；
- `backtest::order_target_value` 以 `lastPrice` 换算目标股数，并把**调整数量**按 100 股向下取整；
- 科创板证券首次建立正持仓时，实际下单目标不得小于 200 股；当前 helper 仍以 100 股为调整步长，
  调用方必须保证首次正目标至少 200，否则插件风控可能拒单；
- helper 不替组合检查多只买单合计现金。先卖后买，并给手续费与价差留现金；
- 清仓、增量买入是否成功只能按订单状态、成交事件和最终持仓确认。

## 预期日志和输出

第一次快照会打印 message Schema、空/当前持仓表 Schema 和单证券字典 keys；随后应看到一个订单的
`4 -> 1` 常见状态路径和一次成交事件。若价格、涨跌停、现金或风控导致拒单，日志会显示 `-1`，不能
把它改写成预期成交。Task 日志用 `get_task_logs` 分页或 `get_task_log_download` 完整下载。

2026-08-15 使用本文原样请求的真实输出为：`orders` 类型 `ANY VECTOR`，其中一个元素类型
`STRING->ANY DICTIONARY`；订单状态依次 4、1，成交数量 100、费用 5；`trade_details` 2 行、
`daily_positions` 7 行、`daily_portfolios` 7 行、`daily_trading_statistics` 1 行。数据更新可能改变
价格，但对象结构和读取方式是本页验证的契约。

工作流成功后仍产生四张默认 Parquet。日志用于确认调用路径和事件结构，最终账务必须按
`arena://docs/backtest/results` 与 `arena://docs/backtest/qa` 对账。本文最后按 DolphinDB Server
`2.00.18`、Backtest `2.00.18.11`、MatchingEngineSimulator `2.00.18.11` 于 2026-08-15 完成
Runtime Schema 与脚本编译验证。
