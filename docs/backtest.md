# Backtest 请求

Backtest 使用 Factor Query 准备候选代码和策略数据，将日线转换为开盘、收盘单档合成快照，并在
DolphinDB Backtest 插件中执行八个生命周期回调。本页定义请求 JSON；回调可用数据、订单、持仓和
事件接口见 `arena://docs/dolphindb-backtest`。

## 调用

```text
create_project(application="backtest", title=...)
run_backtest(project_id=result.id, parameters=<BacktestParameters>)
get_workspace_status(workspace_id) -> SUCCESS
list_workflow_outputs(application="backtest", workflow_instance_id=...)
save_version(application="backtest", project_id=..., workflow_instance_id=..., remark=...)
```

`run_backtest` 会先连接 DolphinDB 编译 `utils` 和 callbacks。编译失败时不会创建 Workspace；编译
成功后返回的 ID 只表示工作流已提交，仍需轮询。

## 顶层字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `config` | object | 否 | 见下文 | 插件资金、费用和可开放选项 |
| `params` | object | 否 | `{}` | 策略参数，通过 `getParams()` 读取 |
| `codes_query` | FactorQuery 或 null | 否 | `null` | 第一阶段候选代码查询 |
| `dataset_query` | FactorQuery | 是 | — | 第二阶段行情和策略数据查询 |
| `adj` | `hfq`、`qfq` 或 null | 否 | `null` | 合成快照价格复权方式 |
| `annual_trading_days` | integer | 否 | `250` | 年化指标使用的交易日数，至少 1 |
| `risk_free_rate` | finite number | 否 | `0.04` | Sharpe 年化无风险收益率 |
| `utils` | string | 否 | `""` | 回调注册前原样执行的 DolphinDB 脚本 |
| `callbacks` | object | 是 | — | 必须且只能包含八个固定回调 |

模型为 strict 且禁止额外顶层字段。不要把数字写成字符串。

## 代码范围与两阶段查询

`codes_query=null` 时：

- `dataset_query.codes` 必须非空；
- 当前股票回测只接受 `.SH` 和 `.SZ` 代码。

`codes_query` 非空时：

1. 执行第一阶段；
2. 对过滤后结果的 `code` 取整个区间的去重并集；
3. 用该并集覆盖 `dataset_query.codes`；
4. 执行第二阶段完整查询。

第一阶段不是每日 join。若成员关系需要逐日生效，应在第二阶段再输出成员 BOOL derivative。为了
让调出股票仍保留在当日 message 中并可卖出，通常不要把该成员条件放入第二阶段 `filters`，而在
回调读取的前一交易日信号中判断。

## `dataset_query`

Runtime 自动补充合成快照需要的基础因子：

```text
open, low, high, close, up_limit, down_limit, pre_close
```

`adj` 非 null 时还会读取 `adj_factor`。调用方不需要把这些列重复写入 `factors`，但
`FactorQuery` 本身仍要求 `factors` 或 `derivatives` 至少一项非空。

保留规则：

- `symbol`、`tradeTime` 由回测框架生成，不能作为 factor 或 derivative；
- `adj` 非 null 时不能定义名为 `adj_factor` 的 derivative；
- derivatives 存在于策略历史数据表，不会自动出现在快照 message；
- 策略时序特征必须由 `lookback` 提供足够历史；
- 未来收益、负 shift 等标签不能作为回测信号。

以下是可直接组成动态候选池和第二阶段信号表的两份完整查询：

```json
{
  "codes_query": {
    "start_date": "2020-01-01",
    "end_date": "2025-12-31",
    "lookback": "PT0S",
    "codes": [],
    "factors": ["weight_000300SH"],
    "derivatives": {
      "is_member": {
        "type": "DIRECT",
        "op": "binary.gt",
        "fields": {"left": "weight_000300SH", "right": 0},
        "params": {}
      }
    },
    "filters": ["is_member"]
  },
  "dataset_query": {
    "start_date": "2020-01-01",
    "end_date": "2025-12-31",
    "lookback": "P120D",
    "codes": [],
    "factors": ["weight_000300SH"],
    "derivatives": {
      "is_member": {
        "type": "DIRECT",
        "op": "binary.gt",
        "fields": {"left": "weight_000300SH", "right": 0},
        "params": {}
      },
      "momentum_20d": {
        "type": "TS",
        "op": "unary.pct_change",
        "fields": {"col": "close"},
        "params": {"periods": 20}
      }
    },
    "filters": []
  }
}
```

第二阶段不按 `is_member` 删行；上文 utils 在前一截面显式检查该列，从而仍能对已调出成分下
清仓目标。

## `config`

默认配置：

```json
{
  "cash": 1000000,
  "commission": 0,
  "tax": 0,
  "enableMinimumPerTransactionFee": true
}
```

Runtime 允许并校验的常用字段：

| 字段 | 类型与约束 | 说明 |
| --- | --- | --- |
| `cash` | finite number > 0 | 初始资金 |
| `commission` | finite number >= 0 | 手续费率 |
| `tax` | finite number >= 0 | 印花税率 |
| `syntheticSpread` | finite number，`0 <= x < 1` | 合成盘口完整相对买卖价差 |
| `latency` | integer >= 0 | 插件订单延时参数 |
| `enableMinimumPerTransactionFee` | boolean | 最低单笔费用 |
| `enableSellCloseRestrict` | boolean | 卖出可用量限制 |
| `outputOrderInfo` | boolean | 输出订单风控信息 |
| `outputQueuePosition` | 0、1 或 2 | 插件队列位置输出选项 |

其余 Runtime 已声明的插件 boolean 选项也按 boolean 校验。`config` 是开放字典，能通过 JSON 校验
不代表某个 DolphinDB 版本或当前快照模式一定支持该选项。

以下字段由 Runtime 强制设置，用户传入会被拒绝：

```text
startDate, endDate, strategyGroup, dataType, msgAsTable, matchingMode,
frequency, callbackForSnapshot, msgAsPiecesOnSnapshot,
matchingRatio, orderBookMatchingRatio
```

实际固定值见 `arena://docs/dolphindb-backtest`。

## `params`

`params` 只上传给策略，不传给插件：

```json
{
  "rebalanceDays": 20,
  "capitalRatio": 0.9,
  "minimumMomentum": 0.05
}
```

在 `initialize` 中调用 `getParams()` 并进行 `long()`、`double()`、`bool()` 等明确类型转换。策略
不得假定缺失 key 会自动获得默认值。需要参数敏感性分析的值应放在这里，而不是硬编码在 utils。

## `utils`

`utils` 是一个 DolphinDB 脚本字符串，在 callbacks 之前原样执行。它可以包含多个函数和确有需要
的顶层语句，不是函数名到源码的字典。callbacks 引用的每个自定义函数必须在同一请求的 `utils`
中定义。

```dos
def rebalanceEqualWeight(mutable context, message, signal) {
    selected = select code
               from signal
               where is_member == true and momentum_20d > 0
    selectedCodes = exec code from selected
    for (rowIndex in 0..(message.rows() - 1)) {
        stockCode = message.symbol[rowIndex]
        if (!(stockCode in selectedCodes)) {
            backtest::order_target(context, message, stockCode, 0l, "exit")
        }
    }
    if (size(selectedCodes) == 0) return
    portfolios = Backtest::getTotalPortfolios(context.engine)
    targetValue = double(portfolios["totalEquity"]) *
        context["capitalRatio"] / size(selectedCodes)
    for (rowIndex in 0..(message.rows() - 1)) {
        stockCode = message.symbol[rowIndex]
        if (stockCode in selectedCodes) {
            backtest::order_target_value(
                context, message, stockCode, targetValue, "entry"
            )
        }
    }
}
```

## `callbacks`

JSON 必须正好包含以下八个 key，且每个值必须是同名完整函数定义：

```dos
def initialize(mutable context)
def beforeTrading(mutable context)
def onBar(mutable context, message, indicator)
def onSnapshot(mutable context, message, indicator)
def onOrder(mutable context, orders)
def onTrade(mutable context, trades)
def afterTrading(mutable context)
def finalize(mutable context)
```

未使用的回调仍要定义并可 `return NULL`。不能改变函数名、参数数量或只传函数体。当前固定模式在
09:30 和 15:00 触发 `onSnapshot`，不触发 `onBar`；准确生命周期见运行契约。

一个与上面 utils 配套的回调对象：

```json
{
  "initialize": "def initialize(mutable context) { params = getParams(); context[\"rebalanceDays\"] = long(params[\"rebalanceDays\"]); context[\"capitalRatio\"] = double(params[\"capitalRatio\"]); context[\"tradingDays\"] = 0l; context[\"rejectedOrders\"] = 0l; context[\"filledShares\"] = 0l }",
  "beforeTrading": "def beforeTrading(mutable context) { Backtest::cancelOrder(context.engine); return NULL }",
  "onBar": "def onBar(mutable context, message, indicator) { return NULL }",
  "onSnapshot": "def onSnapshot(mutable context, message, indicator) { if (message.rows() == 0 || time(message.timestamp[0]) != 09:30:00.000) return; context[\"tradingDays\"] = context[\"tradingDays\"] + 1l; if ((context[\"tradingDays\"] - 1l) % context[\"rebalanceDays\"] != 0l) return; signal = backtest::getLastData(context, message, false); if (signal.rows() == 0) return; rebalanceEqualWeight(context, message, signal) }",
  "onOrder": "def onOrder(mutable context, orders) { if (int(orders[5]) < 0) context[\"rejectedOrders\"] = context[\"rejectedOrders\"] + 1l }",
  "onTrade": "def onTrade(mutable context, trades) { context[\"filledShares\"] = context[\"filledShares\"] + long(trades[3]) }",
  "afterTrading": "def afterTrading(mutable context) { return NULL }",
  "finalize": "def finalize(mutable context) { print(\"回测结束：拒单=\" + string(context[\"rejectedOrders\"]) + \"，成交股数=\" + string(context[\"filledShares\"])) }"
}
```

该对象只是说明序列化格式和接口连接。策略的选股、组合、退出与风控由调用方定义，Arena 不规定
策略类别或数量。

## 完整参数外形

以下省略 DSL 节点内部细节时不能直接提交；它用于展示顶层组合关系：

```json
{
  "project_id": 9,
  "parameters": {
    "config": {
      "cash": 1000000,
      "commission": 0.0003,
      "tax": 0.001,
      "syntheticSpread": 0.001,
      "enableMinimumPerTransactionFee": true,
      "outputOrderInfo": true
    },
    "params": {"rebalanceDays": 20, "capitalRatio": 0.9},
    "codes_query": "<完整 FactorQuery 或 null>",
    "dataset_query": "<完整 FactorQuery>",
    "adj": "hfq",
    "annual_trading_days": 250,
    "risk_free_rate": 0.03,
    "utils": "<完整 DolphinDB 脚本>",
    "callbacks": "<上面的八回调对象>"
  }
}
```

实际提交必须用 JSON object 替换三个占位字符串。

## 输出

普通项目固定请求四个 Parquet：

| 逻辑名 | 文件名 | 内容 |
| --- | --- | --- |
| `trade_details` | `trade_details.parquet` | 委托与成交明细 |
| `daily_positions` | `daily_positions.parquet` | 每日持仓 |
| `daily_portfolios` | `daily_portfolios.parquet` | 每日现金、权益、净值、费用和盈亏 |
| `daily_trading_statistics` | `daily_trading_statistics.parquet` | 每日交易统计 |

Workspace SUCCESS 只说明程序执行完成。验证策略还应检查成交、持仓、资金曲线和 Task 日志。

## 提交前检查

- 静态股票池非空，或第一阶段能产生候选代码；
- 第二阶段仍包含退出持仓所需的代码；
- 所有信号通过 `getLastData` / `getHistoryData` 使用当前日期之前的数据；
- `params` 的 key 均存在并在 initialize 转换类型；
- `utils` 包含 callbacks 引用的所有函数；
- callbacks 恰好八个，名称和参数数量正确；
- 目标仓位合计、费用和 spread 不会系统性造成资金不足；
- 不把 `submitOrder` 返回订单号当作成交结果；
- 不访问 Runtime 会话内部变量或把 DSL derivative 当成 message 列。
