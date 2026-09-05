# 回调与交易对象契约

本页集中说明回调对象的数据形式、字段读取和诊断边界，不提供下单逻辑或完整回测请求。撮合时序和
完整字段见 `arena://docs/backtest/dolphindb`，结果表见 `arena://docs/backtest/results`。

## 对象形式

| 对象 | 当前形式 | 读取方式 |
| --- | --- | --- |
| `message` | TABLE | `schema(message).colDefs` 或 `columnNames(message)` |
| `getLastData` / `getHistoryData` | TABLE | 按列读取；只含当前消息日期以前的数据 |
| 全部持仓 | TABLE | `schema(Backtest::getPosition(engine)).colDefs` |
| 单证券持仓 | DICTIONARY | 先读取 `.keys()`，再按 key 访问 |
| `orders` | ANY VECTOR | 遍历每个 STRING→ANY DICTIONARY 事件 |
| `trades` | ANY VECTOR | 遍历每个 STRING→ANY DICTIONARY 事件 |

`orders[0]` 和 `trades[0]` 都是第一个事件字典，不是某个固定字段。额外输出配置可能增加 key，读取
可选字段前必须检查当前事件的 `keys()`。

## 核心字段

`message` 的核心列是 `symbol`、`timestamp`、`lastPrice`、涨跌停价、昨收以及一档买卖价量。它不包含
`dataset_query` 的 derivatives；历史字段必须通过数据 helper 读取。

`orders` 事件当前可依赖的核心 key：

```text
orderId, symbol, timestamp, qty, price, status, direction,
tradeQty, tradeValue, label, updateTime
```

`trades` 事件当前可依赖的核心 key：

```text
orderId, symbol, tradePrice, tradeQty, tradeValue, totalFee,
totalVolume, totalValue, direction, tradeTime, orderPrice, label
```

`submitOrder` 返回 LONG VECTOR，即使只提交一单也不是标量。可将完整返回值直接传给
`cancelOrder(..., orders=orderIds)`，不能再包成 `[orderIds]`；需要单个订单号时先确认向量非空再取
`orderIds[0]`。返回值只表示提交，成交数量和费用以 `onTrade` 事件及结果表为准。

## 订单状态

| 状态 | 含义 | 终态 |
| ---: | --- | --- |
| `4` | 已报 | 否 |
| `0` | 部分成交 | 否 |
| `1` | 全部成交 | 是 |
| `2` | 撤单成功 | 是 |
| `-1` | 审批或风控拒绝 | 是 |
| `-2` | 撤单拒绝；原订单可能仍活动 | 否 |
| `-3` | 日终或回测结束时未成交失效 | 是 |

收到 `-2` 后必须重新查询活动订单。任何目标调整都应基于真实持仓和活动订单，不能根据提交次数推断
持仓变化。

## 拒单诊断边界

当前保证的是订单事件字段和 `status=-1`。MCP 不会把拒单自动扩展成包含目标金额、可用现金快照和
具体拒绝规则的结构化错误对象。

当 `outputOrderInfo=true` 且插件在结果中提供 `orderInfo` 时，可以读取该文本辅助排查；该字段是否
存在、内容和结构均不应作为稳定契约。若没有 `orderInfo`，只能结合订单字段、提交前自行记录的上下文、
实际持仓、可用现金、活动订单和 Task 日志定位。文档不承诺当前接口无法提供的拒单详情。

## 运行诊断

可在回调中少量输出以下信息到 Task 日志：当前日期、对象类型、Schema/keys、订单号、状态和聚合计数。
日志需要用 `get_task_logs` 分页读到 `has_more=false`，或使用完整日志下载。不要输出 Token、密码或
完整大表，也不要把日志当作结构化成交结果。

最终审计必须使用：

- `trade_details` 的订单生命周期；
- `daily_trading_statistics` 的实际成交汇总，运行中也可从 `onTrade` 读取实际成交；
- `daily_positions` 的实际持仓；
- `daily_portfolios` 的现金、权益和累计费用。
