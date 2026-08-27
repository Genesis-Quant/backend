# DolphinDB Backtest 运行契约

本页只描述 Arena Runtime 当前实际创建的股票回测环境，不是 DolphinDB 语言教程，也不覆盖插件
其它 dataType 或 matchingMode。策略可以依赖本文列出的 message、Runtime helper 和回调事件结构。
可调用的 `Backtest::` 函数、实际返回形式、调用阶段、Runtime 独占接口和禁止接口统一见
`arena://docs/backtest/interfaces`；不得根据插件导出列表自行扩大策略能力。

## 先看能力边界

- 日线输入每天只生成 09:30 和 15:00 两个合成快照；
- 盘口只有一档且数量近似无限，不包含真实深度、成交容量或市场冲击；
- `adj` 只调整执行价格链路，不调整查询字段和历史 helper；
- 历史 helper 保证 `date(time) < 当前消息日期`，但不保证供应商数据不可回溯修订；
- 订单号只表示提交，订单终态、成交、现金和持仓必须分别核验。

因此结果只能按当前合成撮合模型解释，不能直接视为真实市场可复制结果。

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

专项契约：动态数据域见 `arena://docs/backtest/dynamic-pool`，二次规划与目标权重见
`arena://docs/backtest/optimization`，回调对象见 `arena://docs/backtest/callback-data`，输出审计见
`arena://docs/backtest/qa`，函数白名单见 `arena://docs/backtest/interfaces`。这些资源不提供具体策略
或构造。

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
| `onOrder` / `onTrade` | `typestr(events)`，并对每个 `event` 调用 `event.keys()` | 外层是 ANY VECTOR，元素是 STRING->ANY DICTIONARY；必须遍历全部元素 |

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

`outputOrderInfo`、`outputSeqNum`、`outputTradeSeqNum` 等配置会改变部分结果或事件字典的附加 key。
`outputQueuePosition` 只适用于官方文档指定的含逐笔行情模式，不适用于 Arena 当前固定的
`dataType=1`。因此“全部字段”必须绑定到当前 engine 配置；官方静态字段表和运行时自省应一起使用。
Arena 文档下面列出的 message 列和事件字典 key，针对的是 Runtime 当前固定配置。

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

`benchmark` 是可选用户配置，但只接受 `INDEX_CODES` 中以 `.SH` 或 `.SZ` 结尾的指数。Runtime 从
`coreTable` 单独查询该指数的未复权日行情，把 `000300.SH` 之类的请求代码转换为插件使用的
`000300.XSHG`，再与策略快照按时间合并回放。基准快照会出现在 `onSnapshot` 的 `message` 中；
遍历消息下单的策略必须按
自身候选代码或信号截面限制可交易证券，不能把基准指数当成可交易股票。

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
后前复权。`open/low/high/close/upLimitPrice/downLimitPrice/prevClosePrice` 使用相同调整系数。源
`up_limit/down_limit` 允许为空：Runtime 会结合 `high/low` 和 `pre_close` 生成回退值，具体规则见
“停牌、缺价和无法退出”。回退完成后的必要价格为空时，该日线不会生成快照。

### 复权作用范围

`adj` 只改变撮合使用的执行价格链路，不会改写查询字段：

| 对象 | 是否受 `adj` 影响 | 价格尺度 |
| --- | --- | --- |
| `dataset_query` factors/derivatives | 否 | CoreData 查询尺度 |
| `getHistoryData` / `getLastData` | 否 | 与 `dataset_query` 相同 |
| 回调 `message` | 是 | 选择的执行复权尺度 |
| 委托、成交和持仓价格 | 是 | 继承 `message` 执行尺度 |
| 结果表中的估值、成交和组合金额 | 是 | 继承执行链路及插件账户口径 |

凡是把带价格量纲的历史字段与 message、委托、成交或持仓价格混用，调用方必须先按同一代码、同一
有效日期和明确调整因子统一尺度。Arena 不自动完成这一步，字段名相似也不表示尺度相同。无量纲值
仍需确认其上游数据定义一致。

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

`syntheticSpread=0.001` 表示完整价差 10bp，舍入前买卖两侧各约 5bp；当 `lastPrice=10` 时，买一为
9.995，卖一为 10.005。spread=0 时，买一、卖一和 lastPrice 都是 10。
`upLimitPrice/downLimitPrice` 是独立风控边界，不是第二档盘口。

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

message 不包含 `dataset_query.derivatives`，不能把命名 derivative 当作 message 列。历史字段必须
通过下面的数据 helper 读取。当前 Runtime 没有向 `indicator` 注册 DSL derivative。
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

### 从因子日期到成交日期的完整时间线

对交易日 `t`，当前日线合成模式的严格顺序是：

```text
t 之前最后一个实际数据截面收盘
  -> Runtime 已完成该截面的基础字段、派生字段和截面计算
t 日盘前
  -> beforeTrading(context)
t 日 09:30
  -> 引擎接收以 t 日 open 构造的快照并更新最新订单簿
  -> onSnapshot(context, message, indicator)
  -> 回调内提交的 latency=0 可成交限价单立即与这张 09:30 订单簿撮合
  -> onOrder/onTrade 按实际状态变化穿插触发
t 日 15:00
  -> 引擎接收以 t 日 close 构造的快照并更新订单簿
  -> onSnapshot，再处理已有挂单和新订单产生的订单/成交事件
  -> afterTrading(context)
最后一个交易日结束
  -> finalize(context)
```

在 09:30：

- `message.lastPrice` 是当日 `open`，可以用于当前目标股数换算和撮合；
- message 只含本页列出的快照字段，不含当日 `high`、`low`、`close`；
- `getLastData/getHistoryData` 强制 `date(time) < t`，所以不能读取当日收盘后才确定的字段；
- 当日 `high`、`low`、`close` 不能参与 09:30 决策，即使它们曾用于离线构造 message；
- `getLastData` 返回全表最后一个实际存在的时间截面，不是给每只股票分别寻找最后一个非空值。

输出 Parquet 的时间戳采用交易所本地时间，但当前写出为无时区 `timestamp[ns]`。跨系统读取时应显式
按 `Asia/Shanghai` 业务语义解释，不能把它当成 UTC 再转换一次。

## 历史数据 helper

```text
backtest::getHistoryData(context, msg, filter=true, start=NULL, end=NULL)
backtest::getLastData(context, msg, filter=true)
```

- `filter=false`：读取 derivatives 计算后、`dataset_query.filters` 执行前的表；
- `filter=true`：读取 filters 后的表；
- `start`、`end`：可选的 DATE 兼容标量，按闭区间限制返回日期；可只传一侧；
- 两者只返回 `date(time) < date(msg.timestamp[0])`；
- `end` 即使晚于当前回调日，也不会放宽上述严格历史边界；`start > end` 会立即报错；
- `getLastData` 从历史中取得最后一个实际存在的 `time` 截面；
- 返回表含 `dataset_query` 请求的 factors 和命名 derivatives；
- 返回代码已经转换为 `.XSHG/.XSHE`。

`getLastData` 可能返回空表，使用前检查 `rows()`。禁止直接访问 Runtime 会话内部表，禁止使用
`context["coreBacktestComputedData"]`、`context["coreBacktestUnfilteredFactorData"]` 等名称。
这些不是策略 API，且绕开日期边界会引入未来数据。

### 历史行与 rolling/shift

在 t 日 09:30 调用 `getLastData`，读到的是最后一个 `date(time) < t` 的完整截面，通常是 t-1：

```text
t-20 ... t-2  t-1       t 日 09:30 callback / submitOrder
|------ rolling window ------|  读取 t-1 信号；latency=0 时可与 t 日开盘最新盘口即时撮合
```

- 在 t-1 行得到的滚动结果已经只使用不晚于 t-1 的观测；t 日读取该行时，不应为了“避免未来数据”
  无条件再 shift，否则会额外丢掉一个已完成观测；
- 若某项定义要求窗口排除**正在计算的当前行**，应在 DSL 层显式滞后其输入或结果。是否包含当前行
  由具体算符定义决定，应读取 `describe_dsl_operator`，不能根据算符名字猜测；
- 负 shift、未来收益标签或在 09:30 使用当日 high/low/close 都是未来数据。
- `getLastData` 取最后实际存在日期，不保证自然日相邻；依赖严格连续交易日时需检查 signal.time。

## 因子分析预处理模块

Backtest 在编译 `utils` 和 callbacks 前、以及正式运行回测前，都会按顺序执行：

```dos
use factor
use backtest
```

因此策略可以直接调用因子分析实际使用的同一个公开函数，不需要在 `utils` 中复制实现：

```text
factor::factorPreprocess(
    rawFactorTable,
    factorCols,
    nGroups,
    timeCol="time",
    codeCol="code",
    mktmvCol="mktmv",
    industryCol="industry"
)
```

输入契约：

- `rawFactorTable` 必须同时含日期、代码、市值、行业和 `factorCols` 指定的全部因子列；
- `factorCols` 是一个或多个因子列名，推荐传 SYMBOL 向量；
- `nGroups >= 2`；
- 市值列必须可转为 DOUBLE，行业列必须可转为 STRING；
- 输入不能预先存在任一 `<factor>_group` 列。

函数先复制输入表，再按每个日期、每个因子执行与 Factor 工作流完全相同的处理：MAD 去极值、
因子 z-score、将 `log(max(market_value, 1))` z-score 后与行业哑变量一起做截面 OLS、残差再次
z-score，最后按残差
从小到大划分等数量组。返回表保留其它输入列，以处理值替换原因子列，并新增
`<factor>_group`；它不会原位修改传入表。样本不足以完成回归时，对应日期的处理值和分组为 NULL，
不会退回未中性化值。

### 与 Factor 工作流一致的条件

“调用同一个函数”只保证算法实现唯一，不保证任意两次调用结果天然相同。要复现 Factor 工作流，
下列输入必须全部一致：

- 每个日期参与计算的完整代码截面及有效行；
- 原始因子值、市场价值和行业映射；
- 日期、代码、市值、行业列的选择；
- `factorCols`、`nGroups` 及基础数据版本。

Runtime 应用进程启动时加载一次股票元数据并保存在 Python 模块变量中，后续调用直接复用。
Factor 使用 `.SH/.SZ`，Backtest 将相同映射转换为 `.XSHG/.XSHE`，可通过 `getIndustry()` 读取。
Backtest 不会自动
向 `dataset_query` 结果添加 `industry`；调用方必须把该字典按 `code` 显式映射到待处理表，再调用
本函数。映射不同、某日代码域不同或先用 `filters` 删除部分证券，都会改变截面回归、标准化和分组
结果。

DSL 的 `CS controls.neutralize_by` 只按请求给定的控制列计算截面 OLS 残差；它不包含上述 MAD
去极值、两次 z-score、市场价值对数变换、固定行业哑变量处理和分组。因此二者不是等价接口，
不能用 `controls.neutralize_by` 的输出声称复现了 Factor 内置预处理。

### 回测时点边界

`factorPreprocess` 会处理传入表中的所有日期，自身不知道当前回调时刻。回调中必须先通过
`backtest::getHistoryData` 或 `backtest::getLastData` 取得严格早于当前 message 日期的数据，再把
显式行业映射合入该历史表。禁止访问 Runtime 内部完整区间表后交给预处理函数；那会把未来日期纳入
处理，即使最终只读取最后一行也属于未来数据泄漏。

## 参数、交易日期与 context

```text
getParams() -> parameters.params 对应的 DolphinDB 字典
getParam(key) -> parameters.params 中指定 key 的值；缺失时抛错
getIndustry() -> 与 Factor 研究同源的 XSHG/XSHE 代码到行业字典
getTradeDates() -> 当前运行实际回放的有序 DATE 向量
```

四个函数只能在回测运行期间使用，包括 `initialize` 和后续回调。`getTradeDates()` 读取的是当前
运行传给插件的 message 中实际存在的日期，并已去重、升序排列；参数调优或批量研究传入子区间时，
它只返回该次子区间的日期。它不是对交易所官方日历的额外查询，缺少行情的日期不会被补入。

`initialize` 应读取参数并显式转换类型。`getParam(key)` 只接受标量 STRING 或 SYMBOL，并对空 key
或缺失 key 直接抛错；如果需要一次遍历全部参数再使用 `getParams()`。`getIndustry()` 返回 Runtime
应用进程启动时加载到 Python 变量的当前股票行业映射，不是按历史日期变化的 point-in-time 行业分类；字典只包含
Backtest 支持的 `.XSHG/.XSHE` 股票代码。插件提供
`context.engine`，调用方自己的状态必须在 `initialize` 中创建。Arena 不向 context 注入 Factor 表、message、params 或任何
`coreBacktest*` 变量。

```dolphindb
def initialize(mutable context) {
    context["window"] = long(getParam("window"))
    context["tradeDates"] = getTradeDates()
    context["industry"] = getIndustry()
}
```

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

调用前还应检查 `targetValue` 有限。优化输出转换成非负目标时，应先按统一容差截断数值噪声，并在
需要时重新归一化和复核约束；不能把极小负数直接传入 helper。完整数值契约见
`arena://docs/backtest/optimization`。

组合调仓分两阶段：先处理目标为零和所有目标下降的订单，包括保留代码的减仓；等待订单/成交状态
更新后，重新查询真实持仓、可用现金和活动订单，再提交目标上升的订单。卖单已提交不等于现金已经
释放，第二阶段还应为费用、价差、取整和价格变化留出余量。

普通 A 股买入按 100 股整数手；目标 0 可以精确清仓不足一手的剩余持仓。科创板首次建立正持仓时
实际目标不得少于 200 股。当前 `order_target_value` 的调整步长仍固定为 100，调用方必须保证科创板
首次正目标至少 200，否则插件风控可能拒单。对象与诊断边界见
`arena://docs/backtest/callback-data`。

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
| `symbol` | STRING | `.XSHG/.XSHE` 标准代码 |
| `lastDayLongPosition` | LONG | 昨日多头持仓 |
| `lastDayShortPosition` | LONG | 昨日空头持仓 |
| `longPosition` | LONG | 当前多头持仓 |
| `shortPosition` | LONG | 当前空头持仓 |
| `longPositionAvgPrice` | DOUBLE | 当前多头成交均价，执行价格尺度 |
| `shortPositionAvgPrice` | DOUBLE | 当前空头成交均价，执行价格尺度 |
| `todayBuyVolume` | LONG | 当日买入成交数量 |
| `todayBuyValue` | DOUBLE | 当日买入成交额 |
| `todaySellVolume` | LONG | 当日卖出成交数量 |
| `todaySellValue` | DOUBLE | 当日卖出成交额 |

当前部署的持仓表和单证券字典**不能假定存在 `totalValue`**。需要证券当前市值时，用真实
`longPosition` 与同一执行尺度的当前 message 价格相乘；需要组合总权益时从
`portfolios["totalEquity"]` 读取，不要把持仓均价当作当前市价。可用现金不等于总权益。不存在的
代码或空持仓可能返回空/NULL，使用 `.sum()` 后也应 `nullFill(..., 0)`。字段实测和自省请求见
`arena://docs/backtest/callback-data`。

当前股票合成模式下 `getOpenOrders` 的非空结果实测为表。默认字段为：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `orderId` | LONG | 订单 ID |
| `timestamp` | TIMESTAMP | 委托时间 |
| `symbol` | STRING | 证券代码 |
| `price` | DOUBLE | 委托限价 |
| `totalQty` | LONG | 原始委托数量 |
| `openQty` | LONG | 尚未成交数量 |
| `direction` | INT | 1 买开、2 卖开、3 卖平、4 买平 |
| `label` | STRING | 下单时的业务标签 |

插件模式或队列位置配置可能追加以下字段，使用前必须以当前非空返回表的 Schema 为准：

| 可选字段 | 类型 | 说明 |
| --- | --- | --- |
| `isMacthing` | INT | 是否已到达撮合时间；插件字段名即如此拼写 |
| `openVolumeWithBetterPrice` | LONG | 更优价未成交量，仅输出队列位置时存在 |
| `openVolumeWithWorsePrice` | LONG | 更差价未成交量，仅输出队列位置时存在 |
| `openVolumeAtOrderPrice` | LONG | 同价未成交量，仅输出队列位置时存在 |
| `priorOpenVolumeAtOrderPrice` | LONG | 同价且更早的未成交量，仅输出队列位置时存在 |
| `depthVolumeWithBetterPrice` | INT | 更优价档位深度，仅输出队列位置时存在 |
| `updateTime` | TIMESTAMP | 最新更新时间 |

空结果或其他插件模式的返回 form 仍需先判断；不要对空对象直接调用 `schema`。本文诊断版本的默认
非空表实际列为 `orderId,timestamp,symbol,price,totalQty,openQty,direction,label`。

撤单：

```dos
Backtest::cancelOrder(context.engine)
Backtest::cancelOrder(context.engine, stockCode)
Backtest::cancelOrder(context.engine, , orderIds)
Backtest::cancelOrder(context.engine, , , "orderLabel")
```

## `onOrder` 事件

插件通用事件结构见
[Backtest 插件文档](https://docs.dolphindb.com/zh/plugins/backtest.html)。Arena 当前实际收到的 `orders`
是一个 ANY VECTOR，每个元素都是 STRING->ANY DICTIONARY。一次回调可能携带多个事件，必须遍历
`orders`；`orders[0]` 是第一个事件字典，不是 orderId，`orders[5]` 也不是 status。

未启用额外序号字段时，每个事件字典包含：

| key | 类型 | 说明 |
| --- | --- | --- |
| `orderId` | LONG | 订单号 |
| `symbol` | STRING | 证券代码 |
| `timestamp` | TIMESTAMP | 委托时间 |
| `qty` | LONG | 委托数量 |
| `price` | DOUBLE | 委托价 |
| `status` | INT | 状态 |
| `direction` | INT | 方向 |
| `tradeQty` | LONG | 累计成交量 |
| `tradeValue` | DOUBLE | 累计成交额 |
| `label` | STRING | 标签 |
| `updateTime` | TIMESTAMP | 更新时间 |

使用 `event["status"]` 按 key 读取。额外输出选项可能增加 key；读取可选 key 前检查
`key in event.keys()`，不能把字典强制转换成固定位置向量。

完整 status：

| 值 | 状态 | 是否终态 | 调用方处理 |
| ---: | --- | --- | --- |
| `4` | 已报 | 否 | 记录 orderId，等待成交/撤单 |
| `0` | 部分成交 | 否 | 使用累计 `tradeQty`，剩余数量仍挂起 |
| `1` | 全部成交 | 是 | 清除 pending 状态 |
| `2` | 撤单成功 | 是 | 清除 pending 状态 |
| `-1` | 审批/风控拒绝 | 是 | 记录拒单；原因只在可选输出实际提供时可读 |
| `-2` | 撤单拒绝 | 否 | 原订单可能仍活动，重新查询 `getOpenOrders` |
| `-3` | 回测结束仍未成交 | 是 | 计入期末未成交，不能当成已撤单 |

```dos
def onOrder(mutable context, orders) {
    for (event in orders) {
        orderId = long(event[`orderId])
        status = int(event[`status])
        context["orderStates"][orderId] = status
        if (status in [-1, -2, -3]) {
            context["rejectedOrders"] = context["rejectedOrders"] + 1l
        }
    }
}
```

当前订单事件不保证携带目标金额、可用现金快照或具体拒绝规则，MCP 也不会合成这类结构化错误。
`outputOrderInfo=true` 时若结果表实际出现 `orderInfo`，可用其文本辅助排查；否则只能结合提交前记录、
持仓、现金、挂单和日志诊断。

标准处理顺序是：提交后保存 orderId；`status in [4,0]` 时禁止对同一代码重复下目标；每日开始撤销
旧挂单；收到 `2`/`1`/`-1`/`-3` 后清理 pending；收到 `-2` 时以 `getOpenOrders` 的真实结果为准。
部分成交后，任何依赖成交数量或持仓均价的状态都只能在 `onTrade` 后按真实持仓更新，不能在
`submitOrder` 返回时设置。

## `onTrade` 事件

插件通用事件结构见
[Backtest 插件文档](https://docs.dolphindb.com/zh/plugins/backtest.html)；下表是 Arena 当前配置下
实际收到的事件字典 key。

`trades` 同样是 ANY VECTOR，元素是 STRING->ANY DICTIONARY。默认每个事件字典包含：

| key | 类型 | 说明 |
| --- | --- | --- |
| `orderId` | LONG | 订单号 |
| `symbol` | STRING | 证券代码 |
| `tradePrice` | DOUBLE | 本次成交价 |
| `tradeQty` | LONG | 本次成交量 |
| `tradeValue` | DOUBLE | 本次成交额 |
| `totalFee` | DOUBLE | 本次费用 |
| `totalVolume` | LONG | 累计成交量 |
| `totalValue` | DOUBLE | 累计成交额 |
| `direction` | INT | 方向 |
| `tradeTime` | TIMESTAMP | 成交时间 |
| `orderPrice` | DOUBLE | 委托价 |
| `label` | STRING | 标签 |

```dos
def onTrade(mutable context, trades) {
    for (event in trades) {
        context["filledShares"] = context["filledShares"] + long(event[`tradeQty])
        context["paidFees"] = context["paidFees"] + double(event[`totalFee])
    }
}
```

## 动态数据域

合成 `message` 来自第二阶段 filters 后的数据。被 filter 删除或因必要行情缺失而没有快照的代码，
当日不能通过 `order_target*` 调整。第一阶段区间候选并集不代表逐日有效集合；第二阶段必须重新提供
逐日状态，决策只读取严格早于当前日期的截面。需要保留失效代码以继续观察或退出时，不得在第二阶段
提前删行。完整契约见 `arena://docs/backtest/dynamic-pool`。

历史财务字段和成员数据是否经过供应商修订，不由回调日期边界保证。Arena 当前不冻结每次运行的输入
快照；数据来源、填充和 point-in-time 边界见 `arena://docs/overview/dsl`。

## 停牌、缺价和无法退出

源 `up_limit/down_limit` 可以为 NULL。构造合成快照时，Runtime 先按以下规则生成最终涨跌停价：

```text
upLimitPrice = up_limit 非 NULL ? up_limit : max(high, round(pre_close * 1.1, 3))
downLimitPrice = down_limit 非 NULL ? down_limit : min(low, round(pre_close * 0.9, 3))
```

之后 Runtime 才检查同一代码日期的 `open`、`low`、`high`、`close`、最终 `upLimitPrice`、最终
`downLimitPrice` 和 `pre_close`。这些**最终用于生成消息的字段**必须全部非 NULL；任一字段无有效值，
该证券当天的 09:30 与 15:00 快照都不会生成。不能仅凭源 `up_limit/down_limit` 缺失判断快照缺失。

后果是：

1. 该证券不在当日 `message.symbol`；
2. `backtest::order_target*` 找不到它时会抛出“股票不在当前快照中”，策略应先检查是否存在；
3. 已有非目标持仓当天可能无法卖出并继续保留；
4. `daily_positions.closePrice` 可能沿用最近估值价格，不能从持仓表推断当日行情是否有效；
5. 恢复交易后 Runtime 不会自动清仓，只有策略再次在它出现在 message 时提交目标 0 才会退出。

因此“目标集合已经删除该证券”不等于“仓位已经卖出”。结果审计必须检查无法交易期间的持仓延续，
并按 `arena://docs/backtest/results` 对账真实卖平记录。

## 日频合成快照的能力边界

日线输入只生成开盘与收盘两个快照，不能恢复盘中路径或事件先后。09:30 决策不能读取当日
`low/high/close`；订单只能在已有合成快照上观察和撮合，未成交部分等待后续快照。需要真实盘口深度、
盘中路径、成交容量或市场冲击时，必须使用相应粒度的数据和 Runtime 模式；当前 MCP 固定模式不能
表达该精度。

## 证券代码对齐

请求代码使用 `.SH` / `.SZ` 后缀；查询完成后 Runtime 将回测表、message 和历史 helper 中的代码统一
为 `.XSHG` / `.XSHE`。不要在回调里把请求后缀字符串与 `message.symbol` 混比，也不要二次手工转换。
推荐始终从 `message.symbol[rowIndex]` 取得 SYMBOL，
用它筛选 `getLastData` 返回表；只有作为字典 key 或日志文本时再 `string(stockCode)`。

## 运行诊断

### 使用 DOS 输出调试

DOS 输出重定向、Task 日志分页、完整日志下载和敏感信息限制属于所有任务的通用契约，见
`arena://docs/overview/workflows`。Backtest 的 `utils` 和八个 callback 中可以使用少量 `print(...)`
输出日期、行数、订单号或事件摘要；这些内容会进入对应 Task 日志。日志只能证明代码路径和当时变量
值，订单是否真正成交仍必须以 `onOrder`、`onTrade` 和结果表对账。

回测 SUCCESS 但无交易时依次检查：

- `onSnapshot` 的时间条件是否匹配 09:30 或 15:00；
- `getLastData` 是否为空；
- 第二阶段是否把目标股票从 message 删除；
- 目标市值换算后是否小于 100 股；
- 是否存在未撤销挂单；
- `event["status"]`、`trade_details` 是否显示拒单或未成交；
- 现金、可卖持仓、涨跌停、限价、延时和 `syntheticSpread` 是否允许当前或后续盘口成交。

## 结果读取与 QA

四张 Parquet 的字段类型、行粒度、订单事件状态机、费用公式、已知持仓字段限制、标准对账公式和
SUCCESS 后 QA 已集中到 `arena://docs/backtest/results`。执行契约只说明回调中如何产生事件；结果
分析必须再读取结果契约，不能根据 `trade_details` 文件名把它误当成“一笔成交一行”。
