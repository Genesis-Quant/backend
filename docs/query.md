# Query MCP 业务契约

Query 用一份 `FactorQuery` 从 CoreData 读取基础列、计算 DSL 派生列、执行逐日过滤，最终生成
`query.parquet`。本工具只有单阶段查询；不要给 `run_query` 增加 `codes_query`。需要两阶段动态
股票池时使用 Factor 或 Backtest。

## 1. 调用顺序

```text
create_project(application="query", title=...)
  -> structuredContent.result.id
run_query(project_id=id, request=<FactorQuery>)
  -> workspace_id + workflow_instance_id
get_workspace_status(workspace_id) 轮询到 SUCCESS
list_workflow_outputs(application="query", workflow_instance_id=...)
  -> name="data", filename="query.parquet"
```

每位用户最多拥有 5 个 Query 项目。再次运行同一项目会覆盖该项目当前 Workspace 的输入和输出，
Query 没有版本功能。

## 2. `run_query` 输入

工具参数只有两项：

| 参数 | 类型 | 要求 |
| --- | --- | --- |
| `project_id` | integer | `create_project` 返回的 `result.id`，大于 0 |
| `request` | FactorQuery | 完整对象；禁止再包一层 `dataset_query` |

FactorQuery 的字段、引用和过滤规则见 `arena://docs/dsl`，精确类型以
`arena://schemas/query` 为准。

## 3. Query 中股票范围的含义

- `codes` 非空：只读取这些静态代码；
- `codes=[]`：读取全市场，再由 `filters` 逐日过滤；
- 指数成分、PE、停牌状态等随日期变化的条件必须写成 BOOL derivative，并把其名称放入
  `filters`；
- 单阶段 Query 不会先把结果 code 去重再发起第二次查询。若只是想逐日查询沪深300成分，
  单阶段的每日 membership filter 已经足够；若确实需要候选池复用，改用 Factor/Backtest 的
  `codes_query`。

## 4. 构造流程

1. 调用 `list_dsl_operators(search=...)` 查看当前可用基础 factors 和算符摘要；
2. 对每个准备使用的算符调用 `describe_dsl_operator(operator=...)`；
3. 按 `arena://docs/dsl` 构造无环依赖图；
4. 只把返回 `BOOL` 的顶层命名 derivative 放入 `filters`；
5. 根据最长时序依赖设置 `lookback`；
6. 直接把完整 FactorQuery 传给 `run_query`。

不要根据 Python、Pandas 或 TA-Lib 的同名函数猜 `fields`、`params`。模型可校验 JSON 结构，
但不能替你证明某个数据库基础列真实存在；基础列必须来自 `list_dsl_operators` 返回的
`factors`。

## 5. 完整请求示例

目标：2024 年逐日查询沪深300中 PE>5 的股票，输出价格、估值、20日动量、20日收益波动和
当日成分内动量分位数。

```json
{
  "project_id": 12,
  "request": {
    "start_date": "2024-01-01",
    "end_date": "2024-12-31",
    "lookback": "P120D",
    "codes": [],
    "factors": ["close", "pe", "weight_000300SH"],
    "derivatives": {
      "is_hs300": {
        "type": "DIRECT",
        "op": "binary.gt",
        "fields": {"left": "weight_000300SH", "right": 0},
        "params": {}
      },
      "pe_gt_5": {
        "type": "DIRECT",
        "op": "binary.gt",
        "fields": {"left": "pe", "right": 5},
        "params": {}
      },
      "momentum_20d": {
        "type": "TS",
        "op": "unary.pct_change",
        "fields": {"col": "close"},
        "params": {"periods": 20}
      },
      "daily_return": {
        "type": "TS",
        "op": "unary.pct_change",
        "fields": {"col": "close"},
        "params": {"periods": 1}
      },
      "volatility_20d": {
        "type": "TS",
        "op": "unary.rolling_std",
        "fields": {"col": "daily_return"},
        "params": {"window": 20, "min_periods": 20}
      },
      "momentum_rank": {
        "type": "CS",
        "op": "unary.rank_pct",
        "fields": {"col": "momentum_20d"},
        "params": {"ascending": true, "ties_method": "min"},
        "on": "is_hs300"
      }
    },
    "filters": ["is_hs300", "pe_gt_5"]
  }
}
```

字符串 `"weight_000300SH"`、`"pe"` 是列引用；数字 `0`、`5` 是常量。`momentum_rank`
的 `on` 只限制该截面计算，最终删行仍由 `filters` 完成。

## 6. 输出与失败处理

项目运行只请求逻辑输出 `data`：

```json
{
  "application": "query",
  "workflow_instance_id": 321,
  "outputs": [
    {
      "name": "data",
      "filename": "query.parquet",
      "size": 102400,
      "modified_at": "2026-08-12T00:00:00Z",
      "download_url": "https://example/api/v1/query/workflows/321/outputs/data"
    }
  ]
}
```

下载 `download_url` 时仍需相同 Bearer Token。若 Workspace 失败，先读取
`get_workspace_status.result.error`；需要上下文时再用 `get_workflow_details` 获取 Task ID，并用
`get_task_logs` 分页读取完整日志。

## 7. 提交前检查

- 传给 `run_query` 的是 `request`，不是 `parameters` 或 `dataset_query`；
- `factors` 与 `derivatives` 至少一项非空；
- 所有 `filters` 均为顶层 BOOL derivative；
- 没有 derivative 循环依赖；
- TS/CS 所需历史由 `lookback` 覆盖；
- 没有把未来收益或负 shift 用作可交易信号；
- 空 `codes` 是全市场查询，数据规模符合任务意图。
