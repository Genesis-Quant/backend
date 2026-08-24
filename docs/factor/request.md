# Factor 分析请求

Factor 工作流研究一个或多个因子与一个或多个收益标签之间的截面关系。它支持可选的第一阶段
候选池查询，第二阶段生成分析数据，然后计算逐日 IC、Rank IC 和市值加权分组收益。

## 调用

```text
create_project(application="factor", title=...)
run_factor_analysis(project_id=result.id, parameters=<FactorAnalysisParameters>)
get_workspace_status(workspace_id) -> SUCCESS
list_workflow_outputs(application="factor", workflow_instance_id=...)
save_version(application="factor", project_id=..., workflow_instance_id=..., remark=...)
```

项目创建时包含一个可更新的未保存版本。保存版本不是读取结果的前提，但只有当前成功工作流可以
保存。

未保存版本再次运行、保存后创建下一版本、历史 Attempt 参数和结果保留规则见
`arena://docs/factor/api`；通用对象关系见 `arena://docs/overview/projects`。

## 顶层字段

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `codes_query` | FactorQuery 或 null | 否 | `null` | 第一阶段候选池查询 |
| `dataset_query` | FactorQuery | 是 | — | 第二阶段分析数据查询 |
| `factor_columns` | string[] | 是 | — | 待分析因子，至少 1 个 |
| `return_columns` | string[] | 是 | — | 收益标签，至少 1 个 |
| `return_specs` | object | 是 | — | 每个收益标签的口径与覆盖期数，键必须与 `return_columns` 完全一致 |
| `n_groups` | integer | 否 | `5` | 每日等数量分组数，至少 2 |
| `preprocess` | boolean | 否 | `true` | 是否执行 Runtime 内置预处理 |
| `market_value_column` | string | 否 | `circ_mv` | 中性化控制变量和分组收益权重 |

`factor_columns`、`return_columns`、`market_value_column` 不能互相承担冲突角色。Runtime 会把这
些必要列自动加入 `dataset_query.factors`，但如果同名 derivative 已存在则使用 derivative 输出。

## 两阶段查询

当 `codes_query` 非空时：

1. 执行 `codes_query`；
2. 对其过滤后结果的 `code` 去重；
3. 将去重结果设为 `dataset_query.codes`；
4. 执行完整 `dataset_query`，包括其自己的日期条件、derivatives 和 filters。

第一阶段得到的是整个研究区间的候选代码并集，不会把第一阶段每日行过滤自动复制到第二阶段。
需要逐日动态成员关系时，在 `dataset_query` 中再次定义成员 derivative 和 filter。

`codes_query=null` 时直接使用 `dataset_query.codes` 的语义；其中空数组遵循 Query 的全代码域规则。

## 内置预处理

`preprocess=true` 时，Runtime 对每个交易日、每个因子分别处理：

1. 过滤因子、市值或行业无效的行；
2. MAD 去极值；
3. z-score 标准化；
4. 将 `log(max(market_value, 1))` 先 z-score，再与行业哑变量一起做截面 OLS；
5. 将残差再次 z-score；
6. 按残差从小到大划分 `n_groups` 个等数量组。

行业映射在任务运行时从当前股票元数据取得，不由 `dataset_query` 提供。若某日某因子的有效样本
数不足以完成回归，该日该因子的处理值和分组保持空值，不会用未中性化值代替。

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

服务端不会根据列名猜测简单收益、对数收益或覆盖期数。旧请求缺少 `return_specs` 时，仅为兼容历史
数据识别直接的 `unary.log(binary.div(shift(...), shift(...)))` 和 `unary.pct_change` 结构；无法从该结构
精确确定时会明确报错，必须人工补充口径。新请求必须显式提供，避免工作流成功但报告口径错误。

因子分析允许使用未来收益作为标签，但这些列不能再作为回测交易信号。

## 请求构造

精确顶层结构读取 `arena://schemas/factor`。两阶段中的每个 `FactorQuery` 都必须独立满足 Query 契约，
每个 DSL 节点使用 `describe_dsl_operator` 核对。本文不提供具体候选域、因子、标签或分析构造。

## 输出列

逻辑输出 `information_coefficient` 对应 `factor_information_coefficients.parquet`：

```text
time
{factor}_{return}_ic
{factor}_{return}_rank_ic
```

逻辑输出 `group_returns` 对应 `factor_group_returns.parquet`：

```text
time
{factor}_{return}_group0
...
{factor}_{return}_group{n_groups-1}
```

每个请求中的因子与收益列做笛卡尔组合。`group0` 是因子值最低组，最后一组是最高组。分组收益
使用 `market_value_column` 加权。读取结果时必须由实际 `factor_columns` 和 `return_columns`
构造列名，不能硬编码 `ret0`。

## 批量执行

`run_factor_batch` 对应网页研究队列。每项包含唯一 `client_id`、版本 `remark` 和完整
`FactorAnalysisParameters`。一次最多 100 项；成功项会自动保存为独立版本，无需再调用
`save_version`。批量请求外形、自动保存和重试语义见 `arena://docs/factor/api`。

## 提交前检查

- 两阶段都分别满足完整 `FactorQuery` 契约；
- 第二阶段仍包含需要逐日生效的成员和状态过滤；
- `factor_columns`、`return_columns` 与实际输出列同名；
- `return_specs` 与 `return_columns` 一一对应，并与 DSL 实际收益公式一致；
- 收益标签的方向和 shift 符合研究定义；
- `lookback` 覆盖所有滚动窗口；
- 内置预处理的样本数量足以进行市值和行业回归；
- 关闭预处理时已经提供所有 `<factor>_group` 列。
