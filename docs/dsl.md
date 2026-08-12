# Factor Query DSL 构造契约

本文只说明 Arena DSL 的图结构和引用规则。可用算符及其字段不能手写猜测，必须从
`list_dsl_operators` 和 `describe_dsl_operator` 获取。

## 1. FactorQuery

```json
{
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "lookback": "P60D",
  "codes": ["000001.SZ", "600000.SH"],
  "factors": ["close", "pe"],
  "derivatives": {},
  "filters": []
}
```

| 字段 | 业务语义 |
| --- | --- |
| `start_date`、`end_date` | 输出闭区间，严格 `YYYY-MM-DD`，开始不得晚于结束 |
| `lookback` | 只增加计算输入历史；最终输出仍从 `start_date` 开始；不得为负 |
| `codes` | 静态代码范围；空数组表示全市场。两阶段任务中第二阶段会被第一阶段结果覆盖 |
| `factors` | 直接从 CoreData 输出的原始列 |
| `derivatives` | `输出列名 -> Derivative` 的有向无环图 |
| `filters` | BOOL derivative 名称列表；按 AND 过滤最终输出 |

`factors` 与 `derivatives` 至少一项非空。`time`、`code` 是框架列，不能出现在
`factors` 或作为 derivative 名称。名称去除首尾空格后必须唯一。

## 2. Derivative 节点

每个节点统一为：

```json
{
  "type": "TS",
  "op": "unary.pct_change",
  "fields": {"col": "close"},
  "params": {"periods": 20}
}
```

- `type`：`DIRECT` 逐行、`TS` 按 `code` 排序分组、`CS` 按 `time` 截面分组；
- `op`：完整算符名；
- `fields`：输入列、常量、其它命名 derivative，或嵌套 Derivative；
- `params`：算符参数；没有参数也必须传 `{}`；
- TS/CS 算符可能有顶层 `on`，具体以该算符 Schema 为准。

同一个通用算符名可能因 `type` 不同具有不同分组语义。`op` 和 `type` 必须与
`describe_dsl_operator` 返回值一致。

## 3. 算符发现

```text
list_dsl_operators(search="rolling", operator_type="TS")
describe_dsl_operator(operator="unary.rolling_std")
```

`describe_dsl_operator` 返回：

- `type`、`output_kind`；
- `fields` 的精确 JSON Schema；
- `params` 的精确 JSON Schema；
- 是否支持及如何构造 `on`。

不要把其它库的参数名带进来。例如 TA-Lib 风格算符可能使用 `time_period`，Runtime 原生
rolling 算符可能使用 `window`、`min_periods`；只接受返回 Schema 声明的名字。

## 4. 引用和依赖

字符串 operand 的解析顺序：

1. 同名 derivative 输出；
2. 原始 CoreData factor；
3. 否则运行时查询会失败。

derivative 可以引用定义顺序在后的节点，Runtime 会解析依赖；但禁止直接或间接循环：

```text
a -> b -> a   非法
```

filters 只能引用顶层、已命名且静态返回 `BOOL` 的 derivative。数值 derivative 不能作为
filter。逻辑操作数和 `on` 引用也必须是 BOOL。

## 5. `on` 与最终 `filters` 不同

- `on`：只控制某一个 TS/CS 计算参与哪些行；不参与的行该 derivative 输出 NULL；
- `filters`：所有 derivative 计算完成后，删除不满足条件的最终输出行。

例如只在指数成分内计算截面排名，但保留非成分行供其它节点使用：

```json
"momentum_rank": {
  "type": "CS",
  "op": "unary.rank_pct",
  "fields": {"col": "momentum_20d"},
  "params": {"ascending": true, "ties_method": "min"},
  "on": "is_member"
}
```

## 6. lookback

lookback 必须覆盖最长历史依赖，并留出停牌/缺失数据余量。它是日历时长，不是交易 Bar
数量。常见依赖：

- `pct_change(periods=120)`：至少覆盖 120 个有效交易日；
- `rolling_std(window=60)`：至少覆盖 60 个有效交易日；
- 两者串联：覆盖前置收益计算和 rolling 窗口，而不是只取两者最大值。

lookback 不会让早期历史行进入最终 Parquet。

## 7. 完整示例

目标：全市场读取，逐日保留沪深300且 PE>5 的股票；计算20日动量、日收益、20日波动和
指数成分内动量分位数。

```json
{
  "start_date": "2024-01-01",
  "end_date": "2024-12-31",
  "lookback": "P120D",
  "codes": [],
  "factors": ["close", "pe", "weight_000300SH"],
  "derivatives": {
    "is_member": {
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
      "on": "is_member"
    }
  },
  "filters": ["is_member", "pe_gt_5"]
}
```

该示例用于说明结构，不代表任何推荐策略。构造其它算符时重新查询算符 Schema，不要复制
这里的 fields/params。

