# Factor Query DSL

DSL 用 JSON 节点在 DolphinDB 中计算派生列。顶层 `derivatives` 是名称到节点的映射；命名节点会
成为输出列，也可以被其它节点、`filters` 和 `on` 引用。

本页只定义组合规则。当前有哪些算符、每个算符的准确字段与参数，必须通过
`list_dsl_operators` 和 `describe_dsl_operator` 获取。

## 节点结构

```json
{
  "type": "DIRECT | TS | CS",
  "op": "算符完整名称",
  "fields": {},
  "params": {},
  "on": "可选，仅 TS/CS"
}
```

| 字段 | 含义 |
| --- | --- |
| `type` | 计算上下文，必须与算符定义一致 |
| `op` | Runtime 注册的完整算符名 |
| `fields` | 操作数；字段名由具体算符定义 |
| `params` | 非列参数；字段名、类型和默认值由具体算符定义 |
| `on` | TS/CS 可选的 BOOL 条件；DIRECT 禁止 |

即使算符没有参数，`params` 也应传 `{}`。不要把 `fields` 中的列输入移入 `params`，也不要根据
函数名称猜 `periods`、`window`、`min_periods` 等参数。

## 三种计算上下文

- `DIRECT`：逐行计算，不建立时序或截面分组；禁止 `on`。
- `TS`：按 `code` 分组并按 `time` 排序计算。
- `CS`：按 `time` 分组，在同一交易日的代码截面计算。

TS/CS 的 `on` 可为：

- 顶层 BOOL derivative 名称；
- `true` / `false`；
- 返回 BOOL 的嵌套 DSL；
- `null` 或省略，表示不限制输入。

使用 `on` 时，只有条件为 true 的行参与该算符计算；false/NULL 行的算符结果为 NULL。`on` 不会
从最终结果删除行，删行必须使用 Query 顶层 `filters`。

## 操作数

具体算符的 `fields` 通常使用以下一种或多种操作数：

- 字符串：基础 factor 或顶层命名 derivative 的列引用；
- number / boolean：常量；
- object：嵌套 DSL 节点；
- array：多操作数算符的输入列表。

字符串永远按列引用处理。需要字符串字面量时，应使用 Catalog 中明确支持字面量的算符和参数，
不能把普通字符串直接放入数值操作数位置。

示例：

```json
{
  "type": "DIRECT",
  "op": "binary.gt",
  "fields": {"left": "pe", "right": 5},
  "params": {}
}
```

这里 `"pe"` 是列，`5` 是常量。

## 命名与依赖

```json
{
  "derivatives": {
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
    }
  }
}
```

Runtime 从所有 `fields`、嵌套节点和 `on` 收集依赖，按拓扑顺序计算，所以 JSON 对象中的书写顺序
不是依赖保证。循环依赖会在提交前拒绝。

命名规则：

- 去除首尾空格后不能为空或重复；
- 不能使用保留名 `time`、`code`；
- 不能与 `factors` 中的基础输出列同名。

## BOOL 约束

`describe_dsl_operator` 的 `output_kind` 表示静态输出类型。以下位置必须是 BOOL：

- Query 顶层 `filters` 引用的 derivative；
- TS/CS 的 `on`；
- `and`、`or`、`not` 等逻辑算符的操作数；
- 条件选择算符的 condition。

Runtime 会拒绝静态可确定为数值的节点，但基础列本身的数据库类型仍应由调用方根据 Catalog 选择
正确。

## `filters`

`filters` 只能列出顶层命名 BOOL derivative：

```json
{
  "derivatives": {
    "is_member": {
      "type": "DIRECT",
      "op": "binary.gt",
      "fields": {"left": "weight_000300SH", "right": 0},
      "params": {}
    },
    "pe_positive": {
      "type": "DIRECT",
      "op": "binary.gt",
      "fields": {"left": "pe", "right": 0},
      "params": {}
    }
  },
  "filters": ["is_member", "pe_positive"]
}
```

语义为 `is_member AND pe_positive`。若需要 OR，先用逻辑算符构造一个命名 BOOL derivative，再把
该名称放入 `filters`。

## 基础因子与派生依赖

`factors` 表示需要直接输出的基础列。即使某基础列只被 derivative 引用、不在 `factors` 中，
Runtime 也会把它加入内部读取集合，但不会把它作为最终基础输出列。

例如 derivative 引用 `close`，而 `factors=[]`，Runtime 仍读取 `close`。请求仍必须满足
`factors` 与 `derivatives` 至少一项非空。

## 时序边界

滚动、滞后、指数加权和 TA-Lib 算符需要历史数据。通过 `lookback` 加载开始日期之前的历史；最终
输出仍从 `start_date` 开始。`lookback` 是日历时长，不是交易日数量，应为停牌和非交易日留出
余量。

在回测中，负 `shift` 或其它未来数据只能作为分析标签，不能作为策略信号。回测回调读取策略数据
时还必须使用 `getLastData` / `getHistoryData` 的严格历史边界。

## 正确的发现流程

1. `list_dsl_operators(search="close")` 查基础因子和候选算符；
2. `describe_dsl_operator(operator="unary.rolling_mean")` 获取准确 definition；
3. 按 definition 构造节点；
4. 对请求中每个不同 `op` 重复第 2 步；
5. 用对应的 `arena://schemas/query`、`arena://schemas/factor` 或
   `arena://schemas/backtest` 校验顶层对象；
6. 提交给对应 `run_*` 工具。

`arena://dsl/catalog` 提供完整 Catalog，适合一次性加载；仍应以其中每个 operator 的
`definition` 为准。
