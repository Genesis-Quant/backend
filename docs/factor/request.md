# Factor 分析请求

Factor 工作流研究一个或多个因子与一个或多个收益标签之间的截面关系。它支持可选的第一阶段
候选池查询，第二阶段生成分析数据，然后计算逐日 IC、Rank IC 和市值加权分组收益。

## 调用

```text
create_project(application="factor", title=..., parameters=<完整 Factor 请求>)
run_factor_analysis(project_id=result.id, parameters=<完整 Factor 请求>)
get_workspace_status(workspace_id) -> SUCCESS
list_workflow_outputs(application="factor", workflow_instance_id=...)
save_version(application="factor", project_id=..., workflow_instance_id=..., remark=...)
```

项目创建时包含一个可更新的未保存版本。保存版本不是读取结果的前提，但只有当前成功工作流可以
保存。

未保存版本再次运行、保存后创建下一版本、历史 Attempt 参数和结果保留规则见
`arena://docs/factor/api`；通用对象关系见 `arena://docs/overview/projects`。

## 顶层字段

| 字段 | 类型 | 必填 | 网页新建值 | 说明 |
| --- | --- | --- | --- | --- |
| `codes_query` | FactorQuery \| null | 是 | 指数动态池 | 字段必须存在；`null` 表示全市场，非空时为第一阶段候选池查询，支持双源码 |
| `dataset_query` | FactorQuery | 是 | — | 第二阶段分析数据查询，支持双源码 |
| `factor_columns` | string[] | 是 | — | 待分析因子，至少 1 个 |
| `return_columns` | string[] | 是 | — | 收益标签，至少 1 个 |
| `return_specs` | object | 是 | — | 每个收益标签的口径与覆盖期数，键必须与 `return_columns` 完全一致 |
| `n_groups` | integer | 是 | `5` | 每日等数量分组数，至少 2 |
| `n_select` | integer | 是 | `10` | 每日额外选择因子值最小和最大的 N 支股票，至少 1 |
| `preprocess` | boolean | 是 | `true` | 是否执行 Runtime 内置预处理 |
| `market_value_column` | string | 是 | `circ_mv` | 中性化控制变量和分组收益权重 |
| `industry_column` | string | 是 | `industry` | 行业中性化分类列，可选 `industry`、`industry_l0`、`industry_l1`、`industry_l2`、`industry_l3` |

`factor_columns`、`return_columns`、`market_value_column` 不能互相承担冲突角色。启用内置预处理时，
`industry_column` 也不能同时作为因子或收益率列。Backend 会在提交工作流前把这些必要列加入
仅用于本次执行的 Runtime 请求副本；如果同名 derivative 已存在则使用 derivative 输出。用户源码与
持久化参数不会被改写。

使用 `dataset_query.dsl_source` 时，活动源码只负责用户编写的因子和其它过滤逻辑。收益标签节点由
`return_columns` 对应的 `dataset_query.derivatives` 保留并在提交时合并，市值列由请求字段管理，
`stock_pool_member` 则按下文股票池契约注入。JSON/Python 双源码的通用保存和编译规则见
`arena://docs/overview/dsl`。

MCP 新建或修改两阶段查询时优先使用 Python DSL。`codes_query` 调用
`compile_python_dsl(application="query")`，`dataset_query` 调用
`compile_python_dsl(application="factor")`；两份源码都成功后才能提交分析。

## 两阶段查询

使用指数股票池时，`run_factor_analysis` 和 `run_factor_batch` 按两阶段执行：

1. 执行 `codes_query`；
2. 对其过滤后结果的 `code` 去重；
3. 将去重结果设为 `dataset_query.codes`；
4. 执行完整 `dataset_query`，包括其自己的日期条件、derivatives 和 filters。

第一阶段得到的是整个研究区间的候选代码并集，不会把第一阶段的任意估值、流动性或状态过滤自动
复制到第二阶段。指数成分关系是唯一的托管例外：Backend 根据 `codes_query` 选择的指数，在编译
`dataset_query` 时注入同一个 `stock_pool_member` BOOL 节点，并把它放在第二阶段 `filters` 首位，
因此逐日动态成员关系不需要在分析 DSL 中重复定义。其它需要逐日生效的条件仍必须由
`dataset_query` 自己提供。

使用全市场股票池时采用另一种受托管结构：`codes_query=null`、`dataset_query.codes=[]`，并且
源码不定义或过滤 `stock_pool_member`。Backend 在运行参数中注入恒为 true 的同名 BOOL 节点，
因此分析 DSL 可以继续引用它，但不会缩小股票池。Runtime 直接在第二阶段加载全市场代码，请求自身
的其他 `filters` 仍会正常执行。不要用一个任意价格或状态条件伪造全市场成员字段，否则实际基础
截面会被静默缩小。

### MCP/Web 托管股票池契约

网页股票池有两类合法状态：

| 股票池类型 | `codes_query` | `dataset_query.codes` | `dataset_query` 中的 `stock_pool_member` |
| --- | --- | --- | --- |
| 全市场 | `null` | `[]` | 源码不定义；Backend 在运行参数中注入恒真节点但不加入 filter，源码可以引用 |
| 指数动态池 | 定义并过滤固定节点的 FactorQuery | `[]`（运行时写入候选并集） | 源码不定义；Backend 在运行参数中注入并过滤，源码可以引用 |

两种状态都拒绝非空的 `dataset_query.codes`，不接受后再静默覆盖用户指定的静态代码。

选择指数动态池时，客户端只在 `codes_query` 中定义名为 `stock_pool_member` 的节点并把它加入
`codes_query.filters`。该节点是股票池选择的唯一来源，结构固定为：

```json
{
  "stock_pool_member": {
    "type": "DIRECT",
    "op": "binary.gt",
    "fields": { "left": "weight_000852SH", "right": 0 },
    "params": {}
  }
}
```

可选字段与网页股票池一一对应：

| 股票池 | `fields.left` |
| --- | --- |
| 上证 50 | `weight_000016SH` |
| 沪深 300 | `weight_000300SH` |
| 中证 500 | `weight_000905SH` |
| 中证 1000 | `weight_000852SH` |

第一阶段可以另外添加估值、流动性等候选筛选，但不能再用 `is_member` 等别名重复定义指数成员条件。
网页和 MCP 只把 `codes_query.stock_pool_member` 识别为股票池选择；别名会被当作普通派生节点或
filter，可能造成页面显示、复制版本与实际过滤条件不一致。Backend 只强制检查规范节点是否存在、
是否使用受支持指数权重构造以及是否位于 filter 中，无法根据任意名称推断另一个节点是否在语义上
重复了成员条件，因此调用方不得依赖服务端自动识别别名。

### 在分析 DSL 中使用 `stock_pool_member`

`stock_pool_member` 是 Backend 提供给 `dataset_query` DSL 的外部 BOOL 命名节点。
它在保存的 JSON/Python 源码中没有定义，但编译时可用于接受 BOOL 引用的 `fields`，也可作为 TS/CS
算符的 `on`。全市场运行参数注入恒真节点且不使用它过滤；指数动态池运行参数注入实际指数节点并
把它加入 filter。随后两种模式都会执行完整 `FactorQuery` 校验和计算。

JSON DSL 示例：

```json
{
  "factors": [],
  "derivatives": {
    "pool_turnover_rank": {
      "type": "CS",
      "op": "unary.rank_pct",
      "fields": { "col": "turnover_rate_f" },
      "params": { "ascending": true, "ties_method": "average" },
      "on": "stock_pool_member"
    }
  },
  "filters": []
}
```

等价的 Python DSL 核心写法：

```python
pool_turnover_rank = CS.unary.rank_pct(
    "pool_turnover_rank",
    col="turnover_rate_f",
    ascending=True,
    ties_method="average",
    on="stock_pool_member",
)

FACTORS = []
FILTERS = []
```

不要在 `dataset_query.derivatives`、JSON `derivatives` 中自行声明 `stock_pool_member`，也不要在
Python 源码中创建同名 OP，或把它加入分析 DSL 的 `filters`；Backend 会根据 `codes_query` 自动完成。
这个托管节点适用于 Factor 分析和 Backtest 的 `dataset_query`，普通 Query 与两者的
`codes_query` 均不提供同名外部节点。

## 内置预处理

`preprocess=true` 时，Runtime 对每个交易日、每个因子分别处理：

1. 过滤因子、市值或行业无效的行；
2. MAD 去极值；
3. z-score 标准化；
4. 将 `log(max(market_value, 1))` 先 z-score，再与行业哑变量一起做截面 OLS；
5. 将残差再次 z-score；
6. 按残差从小到大划分 `n_groups` 个等数量组。

`industry_column=industry` 时，Runtime 在当前 Python 进程第一次需要股票元数据时读取行业映射，
随后缓存在模块变量中并在该进程生命周期内复用；进程重启后的首次调用会重新读取。该映射不由
`dataset_query` 提供。选择 `industry_l0` 至 `industry_l3` 时，Runtime 改用 CoreData 中按日期
变化的动态行业字段，并自动把该字段加入 `dataset_query`；其中 `industry_l0` 是项目 11 类行业，
另外三个字段分别是申万一至三级分类。若某日某因子的有效样本数不足以完成回归，该日该因子的
处理值和分组保持空值，不会用未中性化值代替。

启用内置预处理时：

- `dataset_query` 不能输出 `industry`；
- 不能预先定义 `<factor>_group`；
- 输出分析所用因子列是中性化后的值。

`preprocess=false` 时 Runtime 不修改因子值，也不生成分组；`dataset_query` 必须为每个因子输出
`<factor>_group`，分组值应为 `0..n_groups-1`。

## 收益标签

收益标签由 DSL 生成，Runtime 不假定固定名称。其字段必须与请求中的 `return_columns` 完全一致，
方向和时间对齐由调用方定义并核验。

`return_specs` 必须逐列说明后处理如何解释收益值：

```json
{
  "return_specs": {
    "future_return": { "kind": "simple", "periods": 1 },
    "future_log_return": { "kind": "log", "periods": 5 }
  }
}
```

- `kind="simple"`：列值是简单收益率；单期财富按 `1 + return` 累乘；
- `kind="log"`：列值是对数收益率；单期财富按收益值累加后取指数；
- `periods`：一个观测覆盖的交易期数，必须为正整数，不是 derivative 名称中的数字或 shift 偏移量；
- `periods=1` 时可以计算累计多空收益、年化收益、年化波动率、Sharpe 和最大回撤；
- `periods>1` 时服务端将观测视为可能重叠。Runtime 仍输出逐日 IC、Rank IC 和原始分组收益，但
  Backend 与浏览器一律不把它们连续复利，相关累计和年化指标返回空值。

ICIR 与 Rank ICIR 始终保留原始观测口径，计算式为 `mean / sample_std`。它们的保存、展示、排序和
比较均不使用 `return_specs.periods`，网页与 API 调用方都不应再做年化转换。

摘要多空收益取每日极端 `top - bottom`，丢弃缺失观测并按日期排序。设有效观测数为 `N`：
`simple` 的日收益为该差值，`log` 则用 `expm1(差值)` 转为简单日收益；财富分别使用累乘或累计后取指数。
年化收益为 `期末财富^(252/N)-1`，年化波动为全部有效日收益（包括首行）的总体标准差
`std(ddof=0)*sqrt(252)`，Sharpe 为年化收益除以年化波动，不减无风险率。
没有有效收益时这些指标为空；仅一条有效收益时总体波动为 0，Sharpe 为空。IC/Rank IC 不足两个有效
观测时样本标准差和 ICIR 为空；标准差为 0 时 ICIR 也为空。以上年化收益口径只用于 `periods=1`。

服务端不会根据列名或 DSL 猜测简单收益、对数收益或覆盖期数。缺少 `return_specs`、键集合与
`return_columns` 不一致，或 `kind` / `periods` 不合法时直接校验失败；读取旧记录时也不迁移、不推断、
不补齐。

因子分析允许使用未来收益作为标签，但这些列不能再作为回测交易信号。

## 请求构造

精确顶层结构读取 `arena://schemas/factor`。`codes_query` 必须独立满足 Query 契约；`dataset_query`
除上述托管 `stock_pool_member` 外必须闭包完整，Backend 注入该节点后还会按完整 `FactorQuery` 再次
校验。每个 DSL 节点使用 `describe_dsl_operator` 核对。Schema 是 Runtime 通用结构；通过 MCP 提交时
还必须满足上面的全市场或指数动态池契约。本文不提供具体因子、标签或分析构造。

第二阶段顶层 derivatives 应只保留分析和过滤真正需要的列：`factor_columns`、`return_columns`、
必要的最终过滤 BOOL，以及确需直接输出或多处复用的列。构成某个因子或收益标签的一次性中间
算术、shift、比较和转换，在算符 definition 允许时必须嵌套进最终命名节点，不能逐步提升为顶层
列。`stock_pool_member` 只在 `codes_query` 中保持前述固定顶层结构；`dataset_query` 源码不重复声明，
但指数动态池下可以把它作为托管 BOOL 名称用于 `on` 或其它接受 BOOL 引用的位置。其它一次性子条件
应嵌套进各自最终的命名过滤 BOOL。

两阶段分别执行并各自生成工作表，因此 `codes_query` 也要使用最小列集合：只需最终筛选 BOOL 时，
把其一次性子条件嵌套到该 BOOL 中。完整的内存、输出列和重复计算权衡见
`arena://docs/overview/dsl` 的“嵌套优先与结果列预算”。

## 输出列

完成 DSL 的 `filters` 后，Runtime 会删除任一 `factor_columns` 列为 `NULL` 的股票行，再执行可选内置
预处理；预处理后再次剔除因子空值。多因子任务先取所有待分析因子共同非空的截面，再标准化、中性化和
分组。未选作分析因子的中间列和未来收益列不参与这层过滤，
因此不会因为未来收益尚不可得而提前删除股票。预处理产生的空因子（如截面无法标准化）也会被剔除。
`processed_data`、IC、分组收益和换手率使用同一份有效因子表，用户 DSL 与保存参数不变。

逻辑输出 `execution_statistics` 对应 `factor_execution_statistics.parquet`，用于检查第二阶段 DSL 的股票域
是否在某个交易日或某个过滤条件处异常收缩：

```text
time
source_count
filter0_count
filter0_name
...
filter{len(dataset_query.filters)-1}_count
filter{len(dataset_query.filters)-1}_name
filter{len(dataset_query.filters)}_count
filter{len(dataset_query.filters)}_name = "因子空值"
filtered_count
retention_rate
```

`time` 每个交易日一行。`source_count` 是正式分析日期区间内、执行 filters 前的去重股票数；
`filter{i}_name` 是 Runtime 实际执行的第 i 个条件，`filter{i}_count` 是累计应用第 0 项至第 i 项后的
剩余股票数。动态股票池的托管条件可能由 Backend 注入，读取端必须使用 name 列，不能只按保存的编辑态
`dataset_query.filters` 推断。最后追加的“因子空值”阶段是预处理完成后所有分析因子共同非空的股票数，
其与前一阶段的差值合计预处理前后剔除的股票数；没有 DSL filters 时，直接与 `source_count` 比较。
`filtered_count` 是包括这层过滤在内的最终有效股票数；`retention_rate = filtered_count / source_count`。
某日全部被剔除时，统计表仍保留该日，最终数量为 0。历史结果不重算；未包含该阶段的旧 Parquet
仍按其原有过滤阶段展示。条件之间可能重叠，
因此不能把各列直接相减后解释为各条件独立命中数；网页仅将相邻阶段的差值解释为该执行顺序下的
边际剔除数。派生计算阶段保持输入行，不生成一个与 `source_count` 重复的计数列。

逻辑输出 `information_coefficient` 对应 `factor_information_coefficients.parquet`：

```text
time
{factor}_{return}_ic
{factor}_{return}_rank_ic
```

逻辑输出 `group_returns` 对应 `factor_group_returns.parquet`：

```text
time
{factor}_{return}_bottom
{factor}_{return}_group0
...
{factor}_{return}_group{n_groups-1}
{factor}_{return}_top
```

每个请求中的因子与收益列做笛卡尔组合。`group0` 是因子值最低组，最后一组是最高组。分组收益
使用 `market_value_column` 加权。`bottom` 和 `top` 分别使用预处理后因子值最小、最大的
`n_select` 支股票，同样按市值加权；它们作为分组曲线的新首尾端，多空指标使用
`top - bottom`。这两列属于必需结果，缺失时结果校验失败，不会退回首尾分组。读取结果时必须由实际
`factor_columns` 和 `return_columns` 构造列名，不能硬编码 `ret0`。

逻辑输出 `group_turnover` 对应 `factor_group_turnover.parquet`：

```text
time
factor
periods
rank_autocorrelation
bottom
group0
...
group{n_groups-1}
top
```

Runtime 从全部 `return_specs` 提取并去重正整数 `periods`，相同持有期只计算一次。`bottom`、`top`
分别对应因子值最小和最大的 `n_select` 支证券，与分组收益的两个极端组合严格使用同一选股规则。
对每个极端组合和等数量分组，换手率遵循 Alphalens 的集合定义：当前组合中不在 `periods` 个交易期前
同一组合的证券数，除以当前组合证券数。前 `periods` 个观测没有比较基准，结果为 `NULL`。`rank_autocorrelation`
是当前截面因子排名与 `periods` 个交易期前排名在共同证券上的 Pearson 相关系数。收益的 `kind`
不影响换手率。普通前端生成的多个相邻单期收益若 `periods` 均为 `1`，只产生一套 1 日换手结果，
不会按收益列名称推断出 1 至 N 日。

## 批量执行

`run_factor_batch` 对应网页研究队列。每项包含唯一 `client_id`、版本 `remark` 和完整
Factor 请求对象；其中的 Query 同样支持双源码。一次最多 100 项；成功项会自动保存为独立版本，无需再调用
`save_version`。批量请求外形、自动保存和重试语义见 `arena://docs/factor/api`。

## 提交前检查

- `codes_query` 独立有效，`dataset_query` 在托管节点注入后满足完整 `FactorQuery` 契约；
- 股票池使用全市场规范，或仅由 `codes_query` 定义并过滤受支持的 `stock_pool_member`；
- `dataset_query` 源码不重复声明托管节点；两种股票池模式都可按需引用 `stock_pool_member`；
- `codes_query` 和 `dataset_query` 的活动 Python 源码分别使用 `query`、`factor` 上下文编译成功；
- 第二阶段仍包含需要逐日生效的其它状态过滤；
- `factor_columns`、`return_columns` 与实际输出列同名；
- 两阶段都已嵌套单次使用的中间节点，没有输出与分析无关的临时列；
- `return_specs` 与 `return_columns` 一一对应，并与 DSL 实际收益公式一致；
- 收益标签的方向和 shift 符合研究定义；
- `lookback` 覆盖所有滚动窗口；
- 内置预处理的样本数量足以进行市值和行业回归；
- 关闭预处理时已经提供所有 `<factor>_group` 列。
