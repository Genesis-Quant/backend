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

对于源自 Tushare 的基础字段，可进一步查阅
[Tushare Pro 数据接口文档](https://tushare.pro/document/2)，核对供应商原始字段的含义、单位、
频率、接口输入输出和更新说明。Tushare 文档是数据源语义参考，不是 Arena 请求 Schema：Worker
可能对字段进行筛选、重命名、类型转换、时间轴对齐或组合派生，因此提交 DSL 前仍必须使用
`arena://dsl/catalog` 确认 Arena 的实际字段名，并可在
[Genesis-Quant/compose 开源仓库](https://github.com/Genesis-Quant/compose) 中核对具体映射实现。

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

## 嵌套优先与结果列预算

构造 DSL 前必须先列出业务真正需要的最终列，并让顶层 `derivatives` 保持最小。Runtime 会把**每个
顶层命名 derivative** 计算为完整向量并追加到计算表。Query 会把它投影进最终 Parquet；Factor 和
Backtest 则会让它继续驻留在交给下游分析或回测的第二阶段数据表中。顶层中间列越多，DolphinDB
工作表以及适用时的 Parquet、下载传输和浏览器 DuckDB 需要保存的列就越多。

嵌套节点会参与求值，但不会成为独立命名列，也不会进入最终结果 Schema。因此，当目标算符的
`fields` 或 TS/CS 的 `on` 按 definition 接受嵌套 DSL 时，必须遵循以下规则：

- 仅被一个下游节点使用、无需单独查看的中间表达式，直接嵌套到该下游节点；
- 仅为一个 TS/CS 节点提供参与掩码的 BOOL 表达式，优先直接嵌套到该节点的 `on`；
- 一个最终过滤条件由多个一次性子条件组成时，只保留最终 BOOL 为顶层 derivative，把子条件嵌套
  到它内部，再由 `filters` 引用最终名称；
- 基础字段只作为嵌套节点输入时，不要为了读取它而放入 `factors`。Runtime 会自动收集并读取依赖，
  `factors` 只列需要直接输出的基础列；
- 不得为了让 JSON 看起来“分步骤”而把每一步算术、比较、转换或掩码都提升为顶层 derivative。

例如，下面的写法会输出三个 BOOL 列：

```json
{
  "derivatives": {
    "left_valid": {
      "type": "DIRECT",
      "op": "binary.gt",
      "fields": { "left": "field_a", "right": 0 },
      "params": {}
    },
    "right_valid": {
      "type": "DIRECT",
      "op": "binary.gt",
      "fields": { "left": "field_b", "right": 0 },
      "params": {}
    },
    "valid": {
      "type": "DIRECT",
      "op": "multiary.and",
      "fields": { "cols": ["left_valid", "right_valid"] },
      "params": {}
    }
  },
  "filters": ["valid"]
}
```

若两个子条件没有其它消费者，应改为只输出最终过滤列：

```json
{
  "derivatives": {
    "valid": {
      "type": "DIRECT",
      "op": "multiary.and",
      "fields": {
        "cols": [
          {
            "type": "DIRECT",
            "op": "binary.gt",
            "fields": { "left": "field_a", "right": 0 },
            "params": {}
          },
          {
            "type": "DIRECT",
            "op": "binary.gt",
            "fields": { "left": "field_b", "right": 0 },
            "params": {}
          }
        ]
      },
      "params": {}
    }
  },
  "filters": ["valid"]
}
```

以下情况才保留顶层命名节点：

- 该列必须出现在结果中，例如 Query 的明确输出、Factor 的 `factor_columns` / `return_columns`、
  Backtest 回调需要从历史 helper 按名称读取的信号列；
- `filters` 需要引用它，因为 `filters` 只能引用顶层 BOOL derivative；
- 同一高成本表达式被多个下游节点复用，命名缓存带来的计算节省明确大于增加一列的常驻内存；
- 调试时确实需要单独核验该列。调试完成后应删除不再需要的顶层中间列。

嵌套表达式每次出现都会独立求值，不会自动按结构去重。不要把同一高成本 TS/CS 节点复制到多个
位置；这种情况下应保留一个有意义的顶层名称并复用。最终目标是在“更少的常驻结果列”和“避免
重复昂贵计算”之间做显式权衡，而不是一律命名或一律复制嵌套。

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

### 单步中性化与完整因子预处理

`CS controls.neutralize_by` 是 DSL 图中的单步截面 OLS 残差算符，不等于 Factor 工作流的完整
预处理。Factor 工作流还会执行 MAD 去极值、标准化、市值对数变换、行业哑变量回归、残差再标准化
和分组。Backtest 已加载同一 `factor` 模块，确需完整一致的算法时应直接调用
`factor::factorPreprocess`；输入表、行业映射、截面代码域和回测时点要求见
`arena://docs/backtest/dolphindb`。不要把两种接口的结果混为一谈。

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

指数权重字段来自 [Tushare Pro 数据接口](https://tushare.pro/document/2) 的 `index_weight`。当前
Worker 对每个自然日以该日作为 `end_date` 查询，取
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
