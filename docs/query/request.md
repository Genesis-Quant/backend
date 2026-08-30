# Query 请求

Query 接收一份支持 JSON/Python 双源码的 `FactorQuery`，读取基础因子、计算派生列、执行逐行过滤，并生成
`query.parquet`。Query 是单阶段接口；`run_query` 没有 `codes_query`。

## 调用

```text
create_project(application="query", title=...)
run_query(project_id=result.id, request=<QueryApplicationRequest>)
get_workspace_status(workspace_id) -> SUCCESS
list_workflow_outputs(application="query", workflow_instance_id=...)
```

再次运行同一 Query 项目会更新当前请求和结果。Query 不支持版本。
Query 项目、运行、历史参数与输出 API 见 `arena://docs/query/api`；Workspace 与 Attempt 的通用关系见
`arena://docs/overview/projects`。

## `FactorQuery` 字段

| 字段 | 类型 | 必填 | 规则 |
| --- | --- | --- | --- |
| `start_date` | string | 是 | 闭区间开始，严格 `YYYY-MM-DD` |
| `end_date` | string | 是 | 闭区间结束，严格 `YYYY-MM-DD`，不得早于开始日期 |
| `lookback` | TimeDelta string | 否 | 默认 0，必须非负；用于开始日期之前的历史计算 |
| `codes` | string[] | 是 | 非空为静态代码范围；空数组表示读取计算区间内存在 `close` 数据的全部代码 |
| `factors` | string[] | 否 | 需要直接输出的基础因子名；仅作为 DSL 输入的字段不要列入 |
| `derivatives` | object | 否 | `{输出列名: DSL节点}`；单次使用的中间节点应嵌套，不要提升为输出列 |
| `filters` | string[] | 否 | 顶层 BOOL derivative 名称；所有过滤条件按 AND 合并 |
| `dsl_source` | object | 否 | 同时保存 JSON/Python 源码；`language` 指定本次执行版本 |

`factors` 与 `derivatives` 至少有一项。列表不允许空字符串或重复项。`time`、`code` 是保留输出列，
不能出现在 `factors` 或作为 derivative 名。`factors` 与 derivative 名不能重叠。

`lookback` 使用 Pydantic TimeDelta 格式，例如 `P30D`、`P1Y`、`PT0S`。Runtime 会从
`start_date - lookback` 开始加载数据，但最终输出只保留 `start_date..end_date`。

传入 `dsl_source` 时，活动源码是 DSL 执行依据；双源码保存、Python 的三个必需结果变量和未选中
源码的处理规则见 `arena://docs/overview/dsl` 的“JSON 与 Python 双源码”。

## 执行顺序

1. 确定计算区间和代码范围；
2. 从 CoreData 读取 `source_factors`；
3. 按依赖拓扑顺序计算命名 derivatives；
4. 对 `filters` 做 AND；
5. 输出 `time`、`code`、请求的 factors 和所有命名 derivatives；
6. 截取到请求的开始和结束日期。

字符串操作数既可能引用基础列，也可能引用前面定义的 derivative。Runtime 会解析依赖并拒绝
循环引用。基础因子是否存在以 `list_dsl_operators` 返回的 `factors` 为准。

## 代码范围

- `codes` 非空时只查询这些代码；
- `codes=[]` 时，Runtime 从 CoreData 中本次计算区间的 `close` 记录取得代码域；
- `.SH`、`.SZ`、`.BJ` 等代码是否有数据取决于 CoreData；Query 本身不限制为回测支持的市场；
- 成分、估值、状态等随日期变化的范围应构造 BOOL derivative 并放入 `filters`。

Query 不会先执行一份查询、将 code 去重、再执行第二份查询。需要两阶段候选池时使用 Factor 或
Backtest 的 `codes_query` + `dataset_query`。

## 请求构造

精确顶层结构读取 `arena://schemas/query`。基础字段先从 DSL Catalog 发现，每个 derivative 的
`fields`、`params`、返回类型和 `on` 约束必须逐项使用 `describe_dsl_operator` 核对。本页不提供具体
筛选条件、因子定义或完整业务请求。

构造请求时先确定最小输出 Schema。只有确实需要下载、复用、过滤或独立核验的列才放在
`factors` / 顶层 `derivatives`；算符 definition 允许时，一次性算术、比较、转换和 `on` 掩码应直接
嵌套。完整规则和命名复用的性能边界见 `arena://docs/overview/dsl` 的“嵌套优先与结果列预算”。

## 输出

逻辑输出名为 `data`，文件名为 `query.parquet`。列包括：

```text
time, code, <factors...>, <命名 derivatives...>
```

`filters` 只删除不满足条件的行，不删除对应 BOOL derivative 列。嵌套但未在 `derivatives` 顶层
命名的节点不形成独立输出列。每增加一个顶层 derivative，都会增加最终 Parquet 的一列；不要把
仅为构造最终字段服务的中间结果一并输出。

## 提交前检查

- `run_query` 使用参数名 `request`；
- 请求没有额外包一层 `dataset_query`；
- 日期格式正确且 `lookback` 覆盖最长时序窗口；
- 每个算符已用 `describe_dsl_operator` 核对；
- 已把允许嵌套且只使用一次的中间节点内联，顶层输出列集合保持最小；
- 所有 `filters` 都引用顶层 BOOL derivative；
- 所有 `on` 字符串都引用顶层 BOOL derivative；
- 没有 derivative 循环依赖；
- 没有将负 shift 或未来收益列作为可交易信号。
