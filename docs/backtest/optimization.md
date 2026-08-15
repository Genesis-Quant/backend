# OSQP 约束组合优化回测

本例不是只列 `osqp` 签名，而是给出目标函数、矩阵维度、状态检查、不可行回退和目标仓位执行的完整
请求。它使用静态股票池，以历史日收益估计均值和协方差，解带满仓等式和单资产上限的均值—方差
问题。动态股票池应先按 `arena://docs/backtest/dynamic-pool` 加入两阶段成员门控，再复用本页求解器。

## 数学契约

资产数为 `n`，权重为 `w`：

```text
minimize    0.5 * w' P w + q' w
P         = 2 * riskAversion * covariance + ridge * I
q         = -expectedReturns
subject to sum(w) = 1
           0 <= w_i <= maxWeight
```

Arena 当前 DolphinDB 中的调用是：

```dos
solution = osqp(q, P, A, l, u)
```

满足 `l <= A*w <= u`。矩阵必须严格为：

| 对象 | 维度 | 本例内容 |
| --- | --- | --- |
| `P` | `n × n` | 对称半正定风险矩阵；ridge 保证数值稳定 |
| `q` | `n` | 负的预期收益 |
| `A` | `(n+1) × n` | 第一行为全 1，其余为单位矩阵 |
| `l` | `n+1` | `[1, 0, ..., 0]` |
| `u` | `n+1` | `[1, maxWeight, ..., maxWeight]` |

返回值是二元 tuple：`solution[0]` 为状态字符串，`solution[1]` 为权重。只把 `solved` 和
`solved inaccurate` 当作有解；其他状态、维度错误、NULL 协方差、观察不足或
`n*maxWeight<1` 时，本例记录失败状态，并对当前 message 中可交易的证券提交全零目标；停牌或缺价而
不在 message 中的旧仓仍只能等待恢复交易，不会静默使用一组违反约束的权重。

DolphinDB 官方定义：
[osqp](https://docs.dolphindb.com/en/3.00.5/Functions/o/osqp.html)、
[quadprog](https://docs.dolphindb.com/en/Functions/q/quadprog.html)。`quadprog(H,f,A,b,Aeq,beq)` 的约束
参数约定与 OSQP 不同，不能只替换函数名；若改用 quadprog，必须重新按其官方定义构造不等式和等式。

## 可直接提交的完整请求

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
    "covarianceWindow": 60,
    "maxAssets": 6,
    "capitalRatio": 0.85,
    "riskAversion": 6.0,
    "maxWeight": 0.35,
    "ridge": 0.000001
  },
  "codes_query": null,
  "dataset_query": {
    "start_date": "2023-01-01",
    "end_date": "2025-12-31",
    "lookback": "P120D",
    "codes": ["000001.SZ", "000333.SZ", "000651.SZ", "600000.SH", "600036.SH", "600276.SH", "600519.SH", "601318.SH"],
    "factors": [],
    "derivatives": {
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
      }
    },
    "filters": []
  },
  "adj": "qfq",
  "annual_trading_days": 250,
  "risk_free_rate": 0.03,
  "utils": "def emptyOptimization(status, assetCount) {\n    result = dict(STRING, ANY)\n    result[`status] = status\n    result[`weights] = take(0.0, assetCount)\n    return result\n}\n\ndef solveMeanVarianceOSQP(expectedReturns, covariance, riskAversion, maxWeight, ridge) {\n    assetCount = size(expectedReturns)\n    if (assetCount == 0 || rows(covariance) != assetCount || cols(covariance) != assetCount) return emptyOptimization(\"invalid_dimensions\", assetCount)\n    if (maxWeight <= 0 || maxWeight * assetCount < 1.0) return emptyOptimization(\"infeasible_weight_cap\", assetCount)\n    if (any(isNull(expectedReturns)) || any(isNull(flatten(covariance)))) return emptyOptimization(\"null_inputs\", assetCount)\n    P = 2.0 * riskAversion * covariance + ridge * eye(assetCount)\n    q = -double(expectedReturns)\n    sumConstraint = matrix(take(1.0, assetCount)).transpose()\n    A = concatMatrix([sumConstraint, eye(assetCount)], false)\n    lowerBounds = 1.0 join take(0.0, assetCount)\n    upperBounds = 1.0 join take(maxWeight, assetCount)\n    optimization = osqp(q, P, A, lowerBounds, upperBounds)\n    status = string(optimization[0])\n    if (!(status in [\"solved\", \"solved inaccurate\"])) return emptyOptimization(status, assetCount)\n    weights = double(optimization[1])\n    weights = iif(weights < 0, 0.0, weights)\n    if (abs(sum(weights) - 1.0) > 0.0001 || max(weights) > maxWeight + 0.0001) return emptyOptimization(\"invalid_solution\", assetCount)\n    result = dict(STRING, ANY)\n    result[`status] = status\n    result[`weights] = weights\n    return result\n}\n\ndef liquidateOptimizationPortfolio(mutable context, message, status) {\n    context[\"lastSolverStatus\"] = status\n    for (rowIndex in 0..(message.rows() - 1)) backtest::order_target(context, message, message.symbol[rowIndex], 0l, \"optimizationFallback\")\n}\n\ndef optimizeAndRebalance(mutable context, message, history) {\n    historyTimes = exec distinct time from history order by time\n    if (size(historyTimes) < context[\"covarianceWindow\"]) { liquidateOptimizationPortfolio(context, message, \"insufficient_history\"); return }\n    signalTime = historyTimes[size(historyTimes) - 1]\n    signal = select code, momentum_60d from history where time == signalTime, momentum_60d > 0, not isNull(return_1d)\n    selectedCount = min(context[\"maxAssets\"], signal.rows())\n    if (selectedCount == 0) { liquidateOptimizationPortfolio(context, message, \"no_eligible_assets\"); return }\n    selectedIndexes = isort(signal.momentum_60d, false)[0:selectedCount]\n    selectedCodes = signal.code[selectedIndexes]\n    riskDates = historyTimes.tail(context[\"covarianceWindow\"])\n    riskHistory = select time, code, return_1d from history where time in riskDates, code in selectedCodes\n    returnMatrix = exec return_1d from riskHistory pivot by time, code\n    returnMatrix = nullFill(returnMatrix, 0.0)\n    assetCodes = symbol(returnMatrix.colNames())\n    optimization = solveMeanVarianceOSQP(avg(returnMatrix), covarMatrix(returnMatrix), context[\"riskAversion\"], context[\"maxWeight\"], context[\"ridge\"])\n    context[\"lastSolverStatus\"] = string(optimization[`status])\n    optimizedWeights = double(optimization[`weights])\n    rowCount = message.rows()\n    targetWeights = take(0.0, rowCount)\n    for (rowIndex in 0..(rowCount - 1)) {\n        assetIndex = find(assetCodes, message.symbol[rowIndex])\n        if (assetIndex < size(assetCodes)) targetWeights[rowIndex] = optimizedWeights[assetIndex]\n    }\n    portfolio = Backtest::getTotalPortfolios(context.engine)\n    totalEquity = double(portfolio[\"totalEquity\"])\n    for (rowIndex in 0..(rowCount - 1)) {\n        if (targetWeights[rowIndex] == 0) backtest::order_target(context, message, message.symbol[rowIndex], 0l, \"optimizationExit\")\n    }\n    for (rowIndex in 0..(rowCount - 1)) {\n        if (targetWeights[rowIndex] > 0) backtest::order_target_value(context, message, message.symbol[rowIndex], totalEquity * context[\"capitalRatio\"] * targetWeights[rowIndex], \"optimizationTarget\")\n    }\n}",
  "callbacks": {
    "initialize": "def initialize(mutable context) { params = getParams(); context[\"tradingDays\"] = 0l; context[\"rebalanceDays\"] = long(params[\"rebalanceDays\"]); context[\"covarianceWindow\"] = long(params[\"covarianceWindow\"]); context[\"maxAssets\"] = long(params[\"maxAssets\"]); context[\"capitalRatio\"] = double(params[\"capitalRatio\"]); context[\"riskAversion\"] = double(params[\"riskAversion\"]); context[\"maxWeight\"] = double(params[\"maxWeight\"]); context[\"ridge\"] = double(params[\"ridge\"]); context[\"lastSolverStatus\"] = \"not_run\" }",
    "beforeTrading": "def beforeTrading(mutable context) { Backtest::cancelOrder(context.engine); return NULL }",
    "onBar": "def onBar(mutable context, message, indicator) { return NULL }",
    "onSnapshot": "def onSnapshot(mutable context, message, indicator) { if (message.rows() == 0 || time(message.timestamp[0]) != 09:30:00.000) return; context[\"tradingDays\"] = context[\"tradingDays\"] + 1l; if ((context[\"tradingDays\"] - 1l) % context[\"rebalanceDays\"] != 0l) return; history = backtest::getHistoryData(context, message, false); if (history.rows() == 0) { liquidateOptimizationPortfolio(context, message, \"empty_history\"); return }; optimizeAndRebalance(context, message, history) }",
    "onOrder": "def onOrder(mutable context, orders) { return NULL }",
    "onTrade": "def onTrade(mutable context, trades) { return NULL }",
    "afterTrading": "def afterTrading(mutable context) { return NULL }",
    "finalize": "def finalize(mutable context) { print(\"OSQP回测完成：最后求解状态=\" + context[\"lastSolverStatus\"]) }"
  }
}
```

## 求解和下单流程

```text
t 日 09:30
  -> getHistoryData(..., false)，只含 date(time) < t
  -> 取最后信号截面，按历史动量选至多 maxAssets
  -> 取最近 covarianceWindow 个历史截面生成收益矩阵
  -> avg / covarMatrix
  -> 构造 P/q/A/l/u 并调用 osqp
  -> 校验状态、权重和、非负与上限
  -> 失败时对当前可交易证券提交全零目标；成功时先清退出仓位，再按目标市值调仓
```

`capitalRatio<1` 为费用和价差留出现金。`order_target_value` 会按当前 message.lastPrice 换算目标股数并
把调整量按 100 股向下取整；它不会替组合预留资金。A 股特殊板块首次正目标和实际风控规则见
`arena://docs/backtest/callback-data`。

## 独立验证求解器

管理员可先用 `execute_dolphindb_script` 执行下面脚本，不创建项目：

```dos
n = 3
P = 2.0 * eye(n)
q = -1.0 * [0.3, 0.2, 0.1]
A = concatMatrix([matrix(take(1.0, n)).transpose(), eye(n)], false)
l = 1.0 join take(0.0, n)
u = 1.0 join take(0.7, n)
solution = osqp(q, P, A, l, u)
[string(solution[0]), solution[1], rows(P), cols(P), rows(A), cols(A)]
```

当前验证结果的状态为 `solved`，权重约 `[0.38333333, 0.33333333, 0.28333333]`，维度依次为
`3,3,4,3`。这是求解器接口测试，不是收益预期。

工作流 SUCCESS 后必须再检查求解状态日志、目标权重、订单拒绝、实际成交和现金。完整 QA 见
`arena://docs/backtest/qa`。本文最后按 DolphinDB Server `2.00.18`、OSQP 当前内置实现、Backtest
`2.00.18.11` 于 2026-08-15 完成函数执行、Runtime Schema 与脚本编译验证。

同日使用本文原样三年请求的真实验证结果：最后求解状态 `solved`，`trade_details` 254 行、
`daily_positions` 3586 行、`daily_portfolios` 727 行、`daily_trading_statistics` 104 行，Runtime
回测完整结束。行数会随基础数据修订变化，不是收益或交易数量保证。
