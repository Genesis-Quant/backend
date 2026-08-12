# Backtest MCP 业务契约

Backtest 工作流先用 Factor Query 生成候选行情和策略数据，再把日频行情交给 DolphinDB Backtest
插件执行八个固定生命周期回调。本文定义 MCP 请求；`message`、历史信号、订单、持仓、资金和
事件字段见 `arena://docs/dolphindb-backtest`。

## 1. 调用顺序

```text
create_project(application="backtest", title=...)
  -> result.id
run_backtest(project_id=id, parameters=<BacktestParameters>)
  -> 先在真实 DolphinDB 编译 utils + 8 callbacks
  -> 编译成功后才创建 Workspace 并提交调度器
get_workspace_status(workspace_id) 轮询到 SUCCESS
list_workflow_outputs(application="backtest", workflow_instance_id=...)
可选 save_version(application="backtest", ...)
```

`run_backtest` 的参数名是 `parameters`。调用前必须读取：

- `arena://schemas/backtest`：精确 JSON Schema；
- `arena://docs/dsl`：两份 Factor Query 的构造；
- `arena://docs/dolphindb-backtest`：Arena 适配后的回调输入和交易接口。

对不确定的通用 DolphinDB 内置函数调用 `describe_dolphindb_functions` 查询当前服务器
`defs()`；不要按 Python、JavaScript 等其它语言的同名函数猜签名。

## 2. BacktestParameters

| 字段 | 类型 | 默认/要求 |
| --- | --- | --- |
| `config` | object | 资金、费用及插件选项；默认 cash=1000000、commission=0、tax=0、启用最低手续费 |
| `params` | object | 策略参数字典；回调通过 `getParams()` 读取 |
| `codes_query` | FactorQuery 或 null | 可选第一阶段候选股票查询 |
| `dataset_query` | FactorQuery | 必填，生成回测行情范围和策略 DSL 数据 |
| `adj` | `hfq`、`qfq` 或 null | 后复权、前复权或不复权 |
| `annual_trading_days` | integer | 默认 250，至少 1，只用于结果年化 |
| `risk_free_rate` | number | 默认 0.04，有限数，只用于 Sharpe 等结果指标 |
| `utils` | string | 回调注册前原样执行的 DOS，可包含多个函数和全局初始化语句 |
| `callbacks` | object | 必须且只能包含八个固定名称，每个值是完整 `def` |

禁止额外字段。`params` 与 `config` 是两套不同字典：前者属于策略，后者属于回测引擎。

## 3. 股票池与两阶段查询

### 静态股票池

设置 `codes_query=null`，且 `dataset_query.codes` 必须非空。当前股票回测只接受 `.SH`、`.SZ`
代码。

### 动态股票池

设置 `codes_query`。第一阶段结果 code 在整个期间取并集、去重，然后覆盖
`dataset_query.codes`。它只限定候选集合，不自动保留每日 membership 语义。

对于“调出指数后卖出”的策略，推荐：

- `codes_query.filters=["stock_pool_member"]`，缩小期间候选集合；
- `dataset_query` 仍输出 `stock_pool_member`，但 `filters=[]`；
- `onBar` 从上一可用截面选择 `stock_pool_member=true` 的目标；
- 对不再入选但仍持有的候选股票把目标数量设为 0。

若在第二阶段也过滤 membership，离开股票池的股票会从行情 message 消失，策略可能无法用当日
价格平仓。

## 4. dataset_query 与无未来数据

Runtime 自动为行情构造读取 `open`、`low`、`high`、`close`、`vol`、`up_limit`、
`down_limit`、`pre_close`；启用复权还会读取 `adj_factor`。这些列不必放入 factors，但
FactorQuery 本身仍要求 `factors` 或 `derivatives` 至少一项。

`symbol`、`tradeTime` 是框架生成列，不能作为 factor 或 derivative 名称。`adj` 非空时不能
再定义名为 `adj_factor` 的 derivative。

回调当天收到的 `message` 只含行情列，不含 DSL 自定义列。策略信号应调用：

```dos
signal = backtest::getLastData(context, message, false)
history = backtest::getHistoryData(context, message, false)
```

二者只返回严格早于当前 message 日期的数据。`false` 读取 dataset filters 前的数据，`true`
读取 filters 后的数据。禁止直接从 context 内完整表读取当前日或未来行构造交易信号。

## 5. config

Runtime 固定并禁止用户传入：

| 字段 | Runtime 值 |
| --- | --- |
| `startDate`、`endDate` | 来自 dataset_query 输出区间 |
| `strategyGroup` | Runtime 生成 |
| `dataType` | 4，日频股票 |
| `msgAsTable` | true |
| `matchingMode` | 2 |

`matchingMode` 不是策略可配置项。允许并由模型校验的常用配置：

| 字段 | 类型/范围 |
| --- | --- |
| `cash` | 有限数且 >0 |
| `commission`、`tax` | 有限数且 >=0 |
| `matchingRatio`、`orderBookMatchingRatio` | 0..1 |
| `frequency`、`latency` | 非负整数 |
| `callbackForSnapshot`、`outputQueuePosition` | 0、1、2 |
| `enableMinimumPerTransactionFee` 等布尔项 | 必须是 JSON boolean |

其它插件配置可能通过 `config` 传入，但模型只对已知字段执行强校验。只有官方插件文档明确支持、
且与 Arena 固定 dataType/matchingMode 兼容时才可使用。

## 6. params、utils、callbacks 的职责

- `params`：可变研究参数，例如窗口、持仓数、资金比例、手数；敏感性分析以 key 构造参数网格；
- `utils`：可复用函数、优化器目标、代码转换、目标仓位计算，不要求“只能包含函数”；
- `initialize`：一次性调用 `getParams()`，完成类型转换和 context 初始化；
- `onBar`：编排信号、目标仓位、真实持仓、撤单/下单；
- `onOrder`/`onTrade`：只在策略确实需要委托/成交状态时处理事件，不能用已发送订单代替真实持仓。

八个回调及参数数量固定：

| 名称 | 完整签名 |
| --- | --- |
| `initialize` | `def initialize(mutable context)` |
| `beforeTrading` | `def beforeTrading(mutable context)` |
| `onBar` | `def onBar(mutable context, message, indicator)` |
| `onSnapshot` | `def onSnapshot(mutable context, message, indicator)` |
| `onOrder` | `def onOrder(mutable context, events)` |
| `onTrade` | `def onTrade(mutable context, events)` |
| `afterTrading` | `def afterTrading(mutable context)` |
| `finalize` | `def finalize(mutable context)` |

未使用回调也必须提供并可 `return NULL`。函数名、大小写和参数数量不允许自定义。

## 7. 完整请求示例

以下示例演示动态股票池、参数化、复用 utils、上一截面信号、真实持仓、资金约束、先卖后买和
事件计数。它是接口示例，不是对研究任务复杂度的上限，也不代表推荐策略。

```json
{
  "project_id": 24,
  "parameters": {
    "config": {
      "cash": 1000000,
      "commission": 0.0003,
      "tax": 0.001,
      "enableMinimumPerTransactionFee": true
    },
    "params": {
      "rebalanceBars": 5,
      "holdingCount": 10,
      "capitalRatio": 0.95,
      "lotSize": 100,
      "minimumMomentum": 0.0
    },
    "codes_query": {
      "start_date": "2020-01-01",
      "end_date": "2026-01-01",
      "lookback": "P0D",
      "codes": [],
      "factors": ["weight_000300SH"],
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
      "end_date": "2026-01-01",
      "lookback": "P240D",
      "codes": [],
      "factors": ["weight_000300SH"],
      "derivatives": {
        "stock_pool_member": {
          "type": "DIRECT",
          "op": "binary.gt",
          "fields": {"left": "weight_000300SH", "right": 0},
          "params": {}
        },
        "momentum_120d": {
          "type": "TS",
          "op": "unary.pct_change",
          "fields": {"col": "close"},
          "params": {"periods": 120}
        }
      },
      "filters": []
    },
    "adj": "hfq",
    "annual_trading_days": 250,
    "risk_free_rate": 0.04,
    "utils": "def arenaCode(symbolValue) { return strReplace(strReplace(string(symbolValue), \".XSHE\", \".SZ\"), \".XSHG\", \".SH\") }\n\ndef currentLongQuantity(context, symbolValue) { position = Backtest::getPosition(context.engine, symbolValue, \"stock\")[\"longPosition\"]; if (count(position) == 0 || isNull(position[0])) return 0l; return long(position[0]) }\n\ndef roundToLot(value, lotSize) { if (value <= 0) return 0l; return long(floor(value / double(lotSize))) * lotSize }",
    "callbacks": {
      "initialize": "def initialize(mutable context) { strategyParams = getParams(); context[\"barCount\"] = 0l; context[\"orderEventCount\"] = 0l; context[\"tradeEventCount\"] = 0l; context[\"rebalanceBars\"] = long(strategyParams[\"rebalanceBars\"]); context[\"holdingCount\"] = long(strategyParams[\"holdingCount\"]); context[\"capitalRatio\"] = double(strategyParams[\"capitalRatio\"]); context[\"lotSize\"] = long(strategyParams[\"lotSize\"]); context[\"minimumMomentum\"] = double(strategyParams[\"minimumMomentum\"]) }",
      "beforeTrading": "def beforeTrading(mutable context) { return NULL }",
      "onBar": "def onBar(mutable context, message, indicator) { context[\"barCount\"] = context[\"barCount\"] + 1l; if ((context[\"barCount\"] - 1l) % context[\"rebalanceBars\"] != 0l) return; signal = backtest::getLastData(context, message, false); if (signal.rows() == 0) return; eligible = select code, momentum_120d from signal where stock_pool_member == true, not isNull(momentum_120d), momentum_120d > context[\"minimumMomentum\"]; selectedCount = min(context[\"holdingCount\"], long(eligible.rows())); selectedCodes = take(\"\", 0); if (selectedCount > 0) { selected = eligible[isort(eligible.momentum_120d, false)[0:selectedCount]]; selectedCodes = string(selected.code) }; rowCount = message.rows(); currentQuantities = take(0l, rowCount); targetQuantities = take(0l, rowCount); prices = double(message.open); equity = double(Backtest::getAvailableCash(context.engine, \"stock\")); for (index in 0..(rowCount - 1)) { currentQuantities[index] = currentLongQuantity(context, message.symbol[index]); equity = equity + double(currentQuantities[index]) * prices[index] }; if (selectedCount > 0) { allocation = equity * context[\"capitalRatio\"] / double(selectedCount); for (index in 0..(rowCount - 1)) { if (arenaCode(message.symbol[index]) in selectedCodes && prices[index] > 0) targetQuantities[index] = roundToLot(allocation / prices[index], context[\"lotSize\"]) } }; for (index in 0..(rowCount - 1)) { difference = targetQuantities[index] - currentQuantities[index]; if (difference < 0) Backtest::submitOrder(context.engine, (message.symbol[index], context.tradeTime, 5, prices[index], -difference, 3), \"rebalanceSell\") }; for (index in 0..(rowCount - 1)) { difference = targetQuantities[index] - currentQuantities[index]; if (difference > 0) Backtest::submitOrder(context.engine, (message.symbol[index], context.tradeTime, 5, prices[index], difference, 1), \"rebalanceBuy\") } }",
      "onSnapshot": "def onSnapshot(mutable context, message, indicator) { return NULL }",
      "onOrder": "def onOrder(mutable context, events) { context[\"orderEventCount\"] = context[\"orderEventCount\"] + long(count(events)) }",
      "onTrade": "def onTrade(mutable context, events) { context[\"tradeEventCount\"] = context[\"tradeEventCount\"] + long(count(events)) }",
      "afterTrading": "def afterTrading(mutable context) { return NULL }",
      "finalize": "def finalize(mutable context) { print(\"bars=\" + string(context[\"barCount\"]) + \" orderEvents=\" + string(context[\"orderEventCount\"]) + \" tradeEvents=\" + string(context[\"tradeEventCount\"])) }"
    }
  }
}
```

此示例只使用已在适配文档中说明的 Arena/Backtest 接口。若自行加入 `find`、优化器、矩阵函数
等通用内置函数，必须先用 `describe_dolphindb_functions` 核对签名。例如当前服务器的
`find` 是 `find(X,Y)`，不是单参数布尔索引函数。

## 8. 预检、运行与输出

`run_backtest` 会在创建 Workspace 前：

1. 连接部署中的 DolphinDB；
2. 加载 Backtest 和 MatchingEngineSimulator 插件及 Runtime `backtest` 模块；
3. 执行/编译 `utils`；
4. 编译八个 callbacks。

语法、函数参数数量或缺失符号会直接作为 MCP tool error 返回，不会生成注定失败的调度任务。
预检只能证明脚本可编译；空数据、数组越界、资金不足、订单拒绝等运行期问题仍需由真实回测发现。

普通项目固定生成四项：

| 输出名 | 文件 | 用途 |
| --- | --- | --- |
| `trade_details` | `trade_details.parquet` | 成交明细 |
| `daily_positions` | `daily_positions.parquet` | 每日证券持仓 |
| `daily_portfolios` | `daily_portfolios.parquet` | 每日现金、市值、总权益、净值 |
| `daily_trading_statistics` | `daily_trading_statistics.parquet` | 每日交易统计 |

Runtime 还支持 `return_summary`、`engine_stat`，但普通项目不会默认请求。工作流成功后可保存版本，
后端会从结果计算摘要指标。

## 9. 提交前检查

- 静态/动态股票池契约满足，动态候选查询不会返回空 code；
- dataset 不过滤掉策略平仓所需的持仓股票；
- 所有交易信号严格来自当前日期之前的数据；
- params 中每个 key 在 initialize 明确读取并转换类型；
- utils 与八个回调都是完整脚本，没有占位符；
- 目标数量按交易单位取整、卖出不超过真实 longPosition；
- 调仓前考虑未成交订单，资金和手续费不会导致系统性拒单；
- 不确定的 DolphinDB 内置函数已通过实时签名工具核对；
- 已理解 `matchingMode=2` 为 Runtime 固定值，未尝试覆盖。
