# Factor MCP 业务契约

Factor 工作流用于研究一个或多个因子与一个或多个未来收益标签的关系。它先生成研究数据，再按
交易日计算 Pearson IC、Rank IC 和市值加权分组收益。输出列名严格由请求中的
`factor_columns`、`return_columns` 拼接，读取报告时不得假定列名叫 `ret0`。

## 1. 调用顺序

```text
create_project(application="factor", title=...)
  -> result.id
run_factor_analysis(project_id=id, parameters=<FactorAnalysisParameters>)
  -> workspace_id + workflow_instance_id
get_workspace_status(workspace_id) 轮询到 SUCCESS
list_workflow_outputs(application="factor", workflow_instance_id=...)
  -> information_coefficient + group_returns
可选 save_version(application="factor", project_id=id, workflow_instance_id=...)
```

`run_factor_analysis` 的参数名是 `parameters`，不能写成 `request`。精确 JSON 类型见
`arena://schemas/factor`；其中两份查询都遵循 `arena://docs/dsl`。

## 2. 参数字段

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| `codes_query` | FactorQuery 或 null | 第一阶段候选股票查询；结果 code 去重后覆盖第二阶段 `codes` |
| `dataset_query` | FactorQuery | 第二阶段研究数据查询，必填；自身 derivatives 和 filters 保留 |
| `factor_columns` | string[] | 待分析因子，至少 1 个、不得重复 |
| `return_columns` | string[] | 未来收益标签，至少 1 个、不得重复 |
| `n_groups` | integer | 分组数量，默认 5，至少 2 |
| `preprocess` | boolean | 默认 true；执行内置去极值、标准化、中性化和分组 |
| `market_value_column` | string | 默认 `circ_mv`；用于中性化及分组收益加权 |

`factor_columns` 与 `return_columns` 不能重叠，`market_value_column` 不能同时扮演这两种角色。
模型会把三者自动补进 `dataset_query.factors`（已由 derivative 生成的名称不会重复添加），但
构造请求时仍应明确检查每个名称确实由第二阶段输出。

## 3. 两阶段动态股票池

第一阶段不是逐日结果与第二阶段逐行 join。实际语义是：

1. 执行 `codes_query`；
2. 对其结果的 `code` 取期间并集并去重；
3. 用这个候选代码集合替换 `dataset_query.codes`；
4. 完整执行 `dataset_query`。

因此，若要求“每一天都必须是沪深300且 PE>5”，membership 与 PE filter 要在两阶段都出现：

- 第一阶段减少期间候选代码；
- 第二阶段保证每日研究截面仍满足条件。

只放在第一阶段会把“期间任何一天满足过条件”的股票带进第二阶段所有日期。两阶段日期范围
通常一致；第二阶段可配置更长 `lookback` 以计算时序因子。

## 4. 未来收益标签

收益标签是 DSL derivative，不是 Arena 固定字段。使用实际命名，例如
`future_1d_log_return`、`future_5d_log_return`。一种清晰构造是先算历史方向的收益，再用负
shift 对齐到当前因子日：

```json
"daily_log_return": {
  "type": "TS",
  "op": "unary.log_return",
  "fields": {"col": "close"},
  "params": {"periods": 1}
},
"future_1d_log_return": {
  "type": "TS",
  "op": "unary.shift",
  "fields": {"col": "daily_log_return"},
  "params": {"periods": -1}
}
```

未来收益只允许作为研究标签，不能被因子、股票池 filter 或实际交易信号引用。使用这些算符前
仍需调用 `describe_dsl_operator` 核对当前 Runtime 的精确 Schema。

## 5. `preprocess`

### `preprocess=true`

Runtime 对每个因子执行 MAD 去极值、标准化、市值与行业中性化，并生成整数分组列
`<factor>_group`。此时：

- `dataset_query` 不得自行输出 `<factor>_group`；
- `dataset_query` 不得自行输出保留列 `industry`；
- 截面样本过少、因子全空或无法完成中性化时，分析可能失败或产生空指标，不能用兜底数值替代。

### `preprocess=false`

Runtime 不做上述预处理。`dataset_query` 必须为每个因子显式输出对应的
`<factor>_group`，否则请求校验失败。

## 6. 完整多因子、多收益示例

目标：研究沪深300且 PE>5 的逐日动态股票池，分析 20 日动量与 5 日反转，对应未来 1 日和
未来 5 日对数收益。

```json
{
  "project_id": 18,
  "parameters": {
    "codes_query": {
      "start_date": "2020-01-01",
      "end_date": "2026-01-01",
      "lookback": "P0D",
      "codes": [],
      "factors": ["weight_000300SH", "pe"],
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
        }
      },
      "filters": ["is_hs300", "pe_gt_5"]
    },
    "dataset_query": {
      "start_date": "2020-01-01",
      "end_date": "2026-01-01",
      "lookback": "P180D",
      "codes": [],
      "factors": ["close", "circ_mv", "weight_000300SH", "pe"],
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
        "return_5d": {
          "type": "TS",
          "op": "unary.pct_change",
          "fields": {"col": "close"},
          "params": {"periods": 5}
        },
        "reversal_5d": {
          "type": "DIRECT",
          "op": "unary.neg",
          "fields": {"col": "return_5d"},
          "params": {}
        },
        "daily_log_return": {
          "type": "TS",
          "op": "unary.log_return",
          "fields": {"col": "close"},
          "params": {"periods": 1}
        },
        "five_day_log_return": {
          "type": "TS",
          "op": "unary.log_return",
          "fields": {"col": "close"},
          "params": {"periods": 5}
        },
        "future_1d_log_return": {
          "type": "TS",
          "op": "unary.shift",
          "fields": {"col": "daily_log_return"},
          "params": {"periods": -1}
        },
        "future_5d_log_return": {
          "type": "TS",
          "op": "unary.shift",
          "fields": {"col": "five_day_log_return"},
          "params": {"periods": -5}
        }
      },
      "filters": ["is_hs300", "pe_gt_5"]
    },
    "factor_columns": ["momentum_20d", "reversal_5d"],
    "return_columns": ["future_1d_log_return", "future_5d_log_return"],
    "n_groups": 5,
    "preprocess": true,
    "market_value_column": "circ_mv"
  }
}
```

`reversal_5d` 明确通过 `unary.neg` 对 5 日收益取负，不依靠名称暗示方向。

## 7. 输出列契约

项目默认生成：

| 输出名 | 文件 | 列命名 |
| --- | --- | --- |
| `information_coefficient` | `factor_information_coefficients.parquet` | `time`；每个组合为 `<factor>_<return>_ic` 和 `<factor>_<return>_rank_ic` |
| `group_returns` | `factor_group_returns.parquet` | `time`；每个组合和分组为 `<factor>_<return>_group0` 到 `group<n_groups-1>` |

以上示例会产生
`momentum_20d_future_1d_log_return_ic`，不会产生 `momentum_20d_ret0_ic`。读取结果时必须从
保存的 `factor_columns`、`return_columns` 生成列名，不能硬编码 `ret0`、`ret1`。

Runtime 还支持中间输出 `processed_data`，但普通 Factor 项目默认只请求上表两项。

## 8. 版本与提交前检查

工作流成功后可调用 `save_version`。保存时后端读取 Parquet、计算每个 factor × return 的摘要
并将当前草稿固化；项目随后生成新的可更新草稿。

提交前确认：

- `codes_query` 的结果不会为空；
- 每日股票池约束已按意图放入第二阶段 filters；
- 所有 factor/return/market-value 名称能由 `dataset_query` 输出；
- 未来收益没有参与因子或过滤逻辑；
- `preprocess` 与 `<factor>_group` 的提供方式一致；
- 报告按真实 factor/return 名称读取输出列。
