# Backtest 端到端生命周期与结果 QA

本页把项目、草稿版本、Workspace、Attempt、Workflow Instance、Task、输出、指标和保存版本串成一个
可执行流程。工作流 `SUCCESS` 只表示代码完成，必须下载四张结果表并通过本页检查后才能采信结果。

## 一次完整运行

```text
1. create_project("backtest", title)
   -> project.id；同时创建 version=1、saved=false 的可更新草稿和一对一 Workspace

2. run_backtest(project_id, complete_parameters)
   -> 先做 Runtime Schema 校验和 DolphinDB 编译预检
   -> 成功后在草稿 Workspace 创建新 Attempt 并提交 DolphinScheduler
   -> 返回 workspace_id 和可能暂为 null 的 workflow_instance_id

3. get_workspace_status(workspace_id)
   -> 始终轮询 Workspace 当前 Attempt，不固定轮询旧 Instance
   -> 失败时先 list_workflow_attempts(workspace_id)，再用 attempt_id 调用 get_workflow_attempt
   -> 有 workflow_instance_id 时继续读取 get_workflow_details、Task 和完整日志

4. SUCCESS 后 list_workflow_outputs("backtest", current_workflow_instance_id)
   -> 必须出现 trade_details、daily_positions、daily_portfolios、daily_trading_statistics
   -> 按 overview/workflows 的安全重定向规则下载并本地归档

5. 执行本页 QA；发现时序、价格尺度、订单、账务或结果缺失时，不保存版本

6. get_project("backtest", project_id)
   -> 确认草稿绑定的仍是刚验证的 Workspace/Instance，避免把迟到结果保存到错误运行

7. save_version("backtest", project_id, current_workflow_instance_id, remark)
   -> 固化当前草稿参数、结果绑定和摘要
   -> 项目自动创建下一个递增、saved=false 的可更新草稿
```

同一草稿再次 `run_backtest` 会在同一 Workspace 创建新 Attempt 并更新当前结果。Attempt 保留提交参数、
状态和事件，但旧 Parquet 不保证永久存在。失败重跑也会创建新 Attempt/Instance；持续使用 Workspace
找当前运行。已保存版本的参数和结果绑定不再被后续草稿覆盖。

## 四张表的最低要求

完整字段、状态码和已知限制见 `arena://docs/backtest/results`。QA 不能只检查文件存在：

| 表 | 最低检查 |
| --- | --- |
| `trade_details` | 按 `orderId` 聚合状态事件；终态集合；拒单、撤单拒绝和 `-3`；direction；不要累加各状态行 tradeQty |
| `daily_positions` | 盘后数量、零仓行、缺行和旧估值价；当前部署不用 `todaySellVolume/Value` 审计卖出 |
| `daily_portfolios` | 日期、现金、市值、权益、净值、累计费用和 ratio；用标准恒等式对账 |
| `daily_trading_statistics` | 买开/卖平实际成交量额；volume>0 时 value/volume≈averagePrice |

当前部署卖出审计以 `daily_trading_statistics.todaySellCloseTradeVolume/Value` 为主，再用
`trade_details.direction=3` 的真实成交状态交叉核对。`trade_details` 是订单事件表，不是成交表。

## 可直接运行的本地 QA 脚本

先按 `arena://docs/overview/workflows` 下载四个文件为当前目录中的同名 Parquet。再从刚验证的
`get_project(...).draft.parameters` 或 `get_version(...).parameters` 取得完整规范化参数对象，原样保存为
当前目录的 `parameters.json`，然后运行下面脚本。它复现 Backend 当前摘要口径，并检查最基本的账务
恒等式；不能把其他运行或默认示例的参数写入该文件。

```python
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

parameters = json.loads(Path("parameters.json").read_text(encoding="utf-8"))
ANNUAL_TRADING_DAYS = int(parameters["annual_trading_days"])
RISK_FREE_RATE = float(parameters["risk_free_rate"])
INITIAL_CASH = float(parameters["config"]["cash"])
ATOL = 0.05

trade_details = pd.read_parquet(Path("trade_details.parquet"))
positions = pd.read_parquet(Path("daily_positions.parquet"))
portfolios = pd.read_parquet(Path("daily_portfolios.parquet")).sort_values("tradeDate", kind="stable")
statistics = pd.read_parquet(Path("daily_trading_statistics.parquet"))

required_portfolio = {
    "tradeDate", "ratio", "cash", "totalMarketValue", "totalEquity", "netValue",
    "totalReturn", "floatingPnl", "realizedPnl", "totalPnl", "totalFee"
}
missing = required_portfolio - set(portfolios.columns)
if missing:
    raise ValueError(f"daily_portfolios missing columns: {sorted(missing)}")
if portfolios.empty:
    raise ValueError("daily_portfolios is empty")
if portfolios["tradeDate"].duplicated().any():
    raise ValueError("daily_portfolios contains duplicate tradeDate")

returns = pd.to_numeric(portfolios["ratio"], errors="coerce").fillna(0).to_numpy(float)
returns = np.where(np.isfinite(returns), returns, 0.0)
wealth = np.cumprod(1.0 + returns)
growth = wealth[-1]
annual_return = growth ** (ANNUAL_TRADING_DAYS / len(returns)) - 1 if growth > 0 else math.nan
annual_volatility = (
    np.std(returns[1:], ddof=0) * np.sqrt(ANNUAL_TRADING_DAYS)
    if len(returns) > 1 else math.nan
)
sharpe = (
    (annual_return - RISK_FREE_RATE) / annual_volatility
    if annual_volatility != 0 else math.nan
)
running_peak = np.maximum.accumulate(np.concatenate(([1.0], wealth)))[1:]
max_drawdown = min(0.0, np.min(wealth / running_peak - 1.0))
period_rate = (1.0 + RISK_FREE_RATE) ** (1.0 / ANNUAL_TRADING_DAYS) - 1.0
excess = returns - period_rate
negative = excess[excess < 0]
downside = np.sqrt(np.square(negative).sum() / len(excess))
sortino = excess.mean() / downside * np.sqrt(ANNUAL_TRADING_DAYS) if downside != 0 else math.nan
nonzero = returns[returns != 0]
win_rate = np.count_nonzero(returns > 0) / len(nonzero) if len(nonzero) else 0.0
fees = pd.to_numeric(portfolios["totalFee"], errors="coerce").to_numpy(float)
previous_fees = np.concatenate(([0.0], fees[:-1]))
fee_increments = fees - np.where(np.isfinite(previous_fees), previous_fees, 0.0)
total_fee = fee_increments[np.isfinite(fee_increments) & (fee_increments >= 0)].sum()

checks = {
    "equity_equals_cash_plus_market_value": bool(np.allclose(
        portfolios["totalEquity"], portfolios["cash"] + portfolios["totalMarketValue"], atol=ATOL, rtol=1e-9
    )),
    "net_value_equals_equity_over_initial_cash": bool(np.allclose(
        portfolios["netValue"], portfolios["totalEquity"] / INITIAL_CASH, atol=1e-8, rtol=1e-8
    )),
    "total_return_equals_net_value_minus_one": bool(np.allclose(
        portfolios["totalReturn"], portfolios["netValue"] - 1.0, atol=1e-8, rtol=1e-8
    )),
    "total_pnl_equals_floating_plus_realized": bool(np.allclose(
        portfolios["totalPnl"], portfolios["floatingPnl"] + portfolios["realizedPnl"], atol=ATOL, rtol=1e-9
    )),
    "cumulative_fee_never_decreases": bool(np.all(np.diff(fees[np.isfinite(fees)]) >= -1e-9)),
}

terminal_states = {1, 2, -1, -3}
orders_without_terminal = []
if not trade_details.empty:
    required_orders = {"orderId", "orderStatus", "direction", "tradeQty", "tradePrice"}
    missing_orders = required_orders - set(trade_details.columns)
    if missing_orders:
        raise ValueError(f"trade_details missing columns: {sorted(missing_orders)}")
    for order_id, events in trade_details.groupby("orderId", sort=False):
        if not set(pd.to_numeric(events["orderStatus"], errors="coerce").dropna().astype(int)) & terminal_states:
            orders_without_terminal.append(int(order_id))

sell_volume = 0
if "todaySellCloseTradeVolume" in statistics:
    sell_volume = int(pd.to_numeric(statistics["todaySellCloseTradeVolume"], errors="coerce").fillna(0).sum())

report = {
    "metrics": {
        "totalReturn": float(growth - 1.0),
        "cagr": float(annual_return),
        "volatility": float(annual_volatility),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "maxDrawdown": float(max_drawdown),
        "winRate": float(win_rate),
        "calmar": None if max_drawdown == 0 else float(annual_return / abs(max_drawdown)),
        "totalFee": float(total_fee),
    },
    "checks": checks,
    "orders": {
        "eventRows": int(len(trade_details)),
        "uniqueOrders": int(trade_details["orderId"].nunique()) if "orderId" in trade_details else 0,
        "ordersWithoutTerminalState": orders_without_terminal,
        "rejectedEvents": int((trade_details.get("orderStatus", pd.Series(dtype=int)) == -1).sum()),
        "sellCloseVolumeFromStatistics": sell_volume,
    },
    "positions": {
        "rows": int(len(positions)),
        "zeroLongPositionRows": int((positions.get("longPosition", pd.Series(dtype=float)) == 0).sum()),
    },
}
print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
if not all(checks.values()) or orders_without_terminal:
    raise SystemExit("QA failed; inspect the report before saving the version")
```

若某指标为非有限值，`allow_nan=False` 会主动报错，而不是输出伪 JSON。无交易、零波动或零回撤时应
根据业务口径明确处理，不要擅自填成 0。

## Backend 当前指标口径

| 指标 | 当前精确定义 |
| --- | --- |
| 总收益 | 全部 `ratio` 先把 NULL/非有限值置 0，`prod(1+ratio)-1` |
| 年化收益 | `(1+totalReturn)^(annualTradingDays/N)-1`，N 包含首行 |
| 年化波动 | 从第二行开始的 `ratio` 总体标准差 `ddof=0 * sqrt(annualTradingDays)` |
| Sharpe | `(annualReturn-riskFreeRate)/annualVolatility`，无风险率是年化值 |
| Sortino | 年化无风险率先换为单期；负超额收益平方和除以**全序列长度**后开方 |
| 胜率 | 非零 `ratio` 中正收益日比例；不是订单或逐笔胜率 |
| 最大回撤 | 财富序列前补初始净值 1，返回最小的 `wealth/runningPeak-1`，不大于 0 |
| Calmar | `annualReturn/abs(maxDrawdown)`；零回撤时为 NULL |
| 总费用 | 累计 `totalFee` 的非负有限日增量之和 |

首日 `ratio` 进入总收益、年化收益、Sortino、胜率和回撤，但从年化波动中排除。对比第三方库时必须
先对齐这一点。`annual_trading_days` 和 `risk_free_rate` 来自版本参数。当前禁止传入 `benchmark`，
没有基准列或基准指标；动态沪深300候选池也不等于沪深300业绩基准。基准比较应另取明确版本的指数
收益并独立计算。

## 撮合现实性检查

当前日线 Runtime 固定在 09:30 和 15:00 两个合成快照，只有一档、每侧十亿股近似无限深度，默认
没有 ADV 参与率或市场冲击模型。`syntheticSpread` 进入买一/卖一价；佣金、印花税和最低费用另计。
涨跌停边界和必要价格缺失仍会影响快照/风控，因此“无限深度”不等于任何订单都成交。正 latency 也
不会创造真实的 09:30:01 行情，订单可能等到下一张 15:00 快照。

每次 QA 还必须确认：

- t 日决策只使用 t 日以前的数据；动态成员每日门控而不是把期间并集当成每日成员；
- `adj` 只调整执行 message，价格型 DSL 指标与持仓/成交价已换到同一尺度；无量纲收益无需换算；
- 指数权重和财务字段可能被供应商回溯修订，Arena 当前不冻结 point-in-time 数据快照；
- 停牌或必要价格缺失的持仓可能无法退出，期末没有自动平仓；
- 普通股票 100 股整数手、科创板首次正目标至少 200 股以及资金/费用拒单均已审计；
- `daily_positions.todaySell*` 当前不能作为卖出事实；卖出使用交易统计和订单/成交事件；
- Token 只发给 Arena origin；允许对象存储预签名重定向，但离开 Arena origin 时移除 Authorization。

本文指标代码与 Backend 当前实现逐项一致，最后按 DolphinDB Server `2.00.18`、Backtest
`2.00.18.11`、MatchingEngineSimulator `2.00.18.11` 于 2026-08-15 验证。
