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
4. 对 `log(max(market_value, 1))` 和行业哑变量做截面 OLS；
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
- 收益标签的方向和 shift 符合研究定义；
- `lookback` 覆盖所有滚动窗口；
- 内置预处理的样本数量足以进行市值和行业回归；
- 关闭预处理时已经提供所有 `<factor>_group` 列。
