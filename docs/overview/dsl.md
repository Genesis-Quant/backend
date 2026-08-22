# Factor Query DSL

DSL 用 JSON 节点在 DolphinDB 中计算派生列。顶层 `derivatives` 是名称到节点的映射；命名节点会
成为输出列，也可以被其它节点、`filters` 和 `on` 引用。

本页只定义组合规则。当前有哪些算符、每个算符的准确字段与参数，必须通过
`list_dsl_operators` 和 `describe_dsl_operator` 获取。

## 获取全部可用基础字段

读取 Resource `arena://dsl/catalog`，返回对象的 `factors` 是当前 Runtime 允许放入
`FactorQuery.factors`、也允许作为 derivative 列引用的**完整基础字段白名单**。该列表在 Backend
启动时由 Runtime `available_factors()` 初始化，当前包含固定 Worker 字段和 `INDEX_CODES` 配置生成
的指数权重字段。

`list_dsl_operators` 的 `result.factors` 也返回同一份完整列表。需要注意：该工具的 `search`、
`operator_type` 和 `limit` 只作用于 `operators`，不会截断或筛选 `factors`。只需要一次性获取全部
字段和算符定义时，优先读取 `arena://dsl/catalog`。`limit` 的合法范围为 1 到 200；匹配数超过
`limit` 时根据 `matched` 与 `returned` 继续缩小 `search` 或按 `operator_type` 查询，不能把一次返回
误当成完整算符集合。

“可用”表示请求能通过 Runtime 的字段白名单校验，不表示每个字段在每只证券、每个日期都存在非
空数据。实际覆盖取决于已更新的数据源、证券类型、上市日期、财报日期和配置的指数代码。

派生字段没有预先存在的全局列表：它们由当前请求的 `derivatives` key 动态命名。一个算符对象中
允许出现哪些 `fields` key、每个 key 接受列引用还是常量，由
`describe_dsl_operator(operator).result.definition` 决定。

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

列引用与常量是否允许、各自位于哪个 `fields` key，必须以目标算符 definition 为准；本页不提供
具体字段或阈值构造。

## 命名与依赖

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

`filters` 只能列出顶层命名 BOOL derivative，多个名称按 AND 合并。若需要其他逻辑关系，先用逻辑
算符生成一个命名 BOOL derivative，再把该名称放入 `filters`。

### 横截面结果的复现边界

`CS` 算符的排名、标准化、分位数和 Top-N 结果取决于当日参与计算的**完整代码域**。复现某个结果时
必须保持代码范围、日期、基础数据版本、所有上游派生列、`on` 掩码和 `filters` 一致：

- `on` 只限制该 CS 节点参与计算的行，不会从最终结果删除未参与行；
- `filters` 在全部命名 derivatives 计算完成后执行；
- 只查询最终保留下来的少量代码，无法复现原完整参与域的横截面结果；
- 对两个不同代码子集分别运行相同 rank 请求，会得到不同分位值；
- 需要全市场基础域时使用 `codes=[]`，再以逐日成员 BOOL 作为 CS 的 `on`；需要删除非成员输出时，
  最后再把该 BOOL 放入 `filters`；
- 若最终输出需要保留未参与某个 CS 节点的行，不要把参与条件加入顶层 `filters`；只用 `on` 限制
  该节点的计算域。

因此复现横截面结果不能只下载最终交易证券的数据，必须保留或重新查询原计算截面。

## 基础字段填充与 point-in-time 边界

Runtime 在计算任何 derivative 和执行 `filters` **之前**，对部分基础字段执行固定填充：

- `weight_*`：某日期至少观察到一条该指数权重时，该日期其他查询代码的缺失权重先填 0；随后按
  `code`、`time` 前向填充；
- 财务字段：按 `code`、`time` 前向填充已经进入 CoreData 的历史记录；财务报表使用供应商
  `f_ann_date`，财务指标使用 `ann_date` 作为 `time`，不是直接用报告期结束日；
- `is_st`：查询轴上的缺失值当前直接填 0；
- 其他基础字段：不做隐式前向填充，除非请求显式使用相应 DSL 算符。

查询会先构造代码与日期轴，再把源数据连接到该轴。源表中字段本身为 NULL 和源表没有该
代码/日期/字段记录，连接后都会先物化为查询轴上的 NULL；当前输出没有来源标记，无法区分这两种
存储状态。随后，上述固定填充可能再把该 NULL 变成 0、前一有效值或继续保留为 NULL。

因此最终输出中只能可靠解释填充后的值：成员字段 `>0` 表示该日按填充规则认定为成员，`0` 表示
按规则认定为非成员，`NULL` 表示填充后仍无有效值。不能从最终 NULL 反推源表究竟存有 NULL，还是
根本没有该代码/日期/字段记录；需要这种来源信息时必须另行查询或保存源数据元数据。

指数权重字段来自 Tushare `index_weight`。当前 Worker 对每个自然日以该日作为 `end_date` 查询，取
响应中不晚于该日的最新 `trade_date` 权重快照，并以当前自然日写入 CoreData。当前存储没有保留
供应商公告时间、抓取时间、版本号或修订批次；重新抓取历史区间可能收到供应商事后修订的数据。
Arena 能说明当前抓取和填充规则，但不能据此证明权重是不可修订的严格历史时点版本。需要审计级
point-in-time 复现时，应另行归档供应商原始响应、抓取时间和数据版本。

财务 Worker 会先按公告日整理供应商当前返回的报表，再把公告日写入查询时间轴；查询层只会从该日
起向后填充。因此它不直接把报告期值放到报告期结束日。这个规则仍不等于不可修订的 point-in-time
快照：当前存储不暴露供应商修订批次，也不为每次运行保留原始响应版本。重新抓取后，历史公告记录
可能反映供应商当前回溯值。

## 基础因子与派生依赖

`factors` 表示需要直接输出的基础列。即使某基础列只被 derivative 引用、不在 `factors` 中，
Runtime 也会把它加入内部读取集合，但不会把它作为最终基础输出列。

例如 derivative 引用 `close`，而 `factors=[]`，Runtime 仍读取 `close`。请求仍必须满足
`factors` 与 `derivatives` 至少一项非空。

## 时序边界

滚动、滞后、指数加权和 TA-Lib 算符需要历史数据。通过 `lookback` 加载开始日期之前的历史；最终
输出仍从 `start_date` 开始。`lookback` 是日历时长，不是交易日数量，应为停牌和非交易日留出
余量。

在回测中，负 `shift` 或其它未来数据只能作为分析标签，不能作为决策输入。回测回调读取历史数据
时还必须使用 `getLastData` / `getHistoryData` 的严格历史边界。

## 退化截面和归一化结果

z-score、robust z-score、L1/L2/sum normalization 等算符遇到零尺度或零分母时，会返回与输入等长的
DOUBLE NULL 向量。它们不会返回标量 NULL，也不会用 0、原值或任意常量替代。下游 derivative、
`on` 和 `filters` 必须按 NULL 语义处理；需要其它退化规则时应在 DSL 中显式定义。

## 正确的发现流程

1. 读取 `arena://dsl/catalog` 的 `factors`，确认所有基础列都在白名单中；
2. 用 `list_dsl_operators(search=<关键词>)` 搜索候选算符；
3. `describe_dsl_operator(operator=<完整算符名>)` 获取准确 definition；
4. 按 definition 构造节点，并对请求中每个不同 `op` 重复第 3 步；
5. 用对应的 `arena://schemas/query`、`arena://schemas/factor` 或
   `arena://schemas/backtest` 校验顶层对象；
6. 提交给对应 `run_*` 工具。

`arena://dsl/catalog` 提供完整 Catalog，适合一次性加载；仍应以其中每个 operator 的
`definition` 为准。
