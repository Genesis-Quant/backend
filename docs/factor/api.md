# Factor API

本文件说明因子研究的项目、运行、版本、批量执行和输出 API。请求字段见
`arena://docs/factor/request`，DSL 见 `arena://docs/overview/dsl`，Schema 见
`arena://schemas/factor`。通用工作流诊断见 `arena://docs/overview/workflows`。

## 项目与版本

创建 Factor 项目会同时创建当前未保存版本。未保存版本有版本号和一对一 Workspace，可重复运行；
保存后参数、成功 Instance、指标与结果固定，并自动创建下一未保存版本。

网页地址：`{ARENA_WEB_URL}/factor/projects/{project_id}`。

```text
list_projects("factor", page=1, page_size=20, search=null, sort_by="updated_at", sort_order="desc")
create_project("factor", title)
get_project("factor", project_id)
update_project("factor", project_id, title)
delete_project("factor", project_id)
```

`search` 按项目名称或 ID 片段过滤，`sort_order` 为 `asc` 或 `desc`。`sort_by` 可选 `id`、`title`、
`latest_version`、`ic_mean`、`rank_ic_mean`、`ic_ir`、`long_short_cumulative_return`、`long_short_annual_return`、
`long_short_sharpe`、`updated_at`。

列表项中的 `latest_metric.ic_ir` 是 `IC 均值 / IC 样本标准差`。网页直接展示该值，按 `ic_ir`
排序时服务端也直接使用该值；ICIR 不进行年化处理。

`get_project` 返回当前 `draft`、最新保存版本和当前工作流信息。`update_project` 只改标题，不改参数、
版本、Workspace 或结果。

## 执行一次因子分析

```text
run_factor_analysis(project_id, parameters)
```

`parameters` 必须直接是完整 Factor 请求对象，不能只提交局部 override。`codes_query` 和
`dataset_query` 均支持 JSON/Python 双源码，活动版本由各自的 `dsl_source.language` 决定。机器可读
基础结构以 `arena://schemas/factor` 为准；MCP 支持全市场与指数动态池两种托管结构。全市场使用
`codes_query=null` 和 `dataset_query.codes=[]`；指数池只在 `codes_query` 定义并过滤
`stock_pool_member`。Backend 会向 `dataset_query` 运行参数注入托管节点：全市场为恒真且不加入
filter，指数池为同一指数节点并加入 filter。分析 DSL 在两种模式下都可以在 `on` 或其它 BOOL 引用
位置使用该名称，无需也不应重复定义。固定结构、支持的股票池字段和示例见
`arena://docs/factor/request`。

服务端严格校验请求，返回 `workspace_id` 和可能暂为空的 `workflow_instance_id`。用
`get_workspace_status(workspace_id)` 轮询当前 Attempt。

## 版本读取与保存

```text
list_versions(application="factor", project_id)
get_version(application="factor", project_id, version)
save_version(application="factor", project_id, workflow_instance_id, remark="")
update_version(application="factor", project_id, version, remark)
delete_version(application="factor", project_id, version)
```

- `list_versions` 同时返回已保存版本和当前未保存版本。
- `get_version` 返回 parameters、Workspace/Instance 绑定、`saved`、`is_current`、remark 和 metrics。
- `save_version` 只能保存当前未保存版本的当前成功 Workflow Instance；不能拿历史 Instance 覆盖版本。
- 保存成功后自动产生下一个未保存版本。
- `update_version` 只修改显示备注，不修改参数和结果。
- 两个删除工具分别要求个人主页中的 Factor 项目和 Factor 版本权限。当前未保存版本不能单独删除，
  活动工作流关联的对象也会按业务规则拒绝；删除已保存版本会永久留下版本号空缺。

## 批量执行

```text
run_factor_batch(project_id, items)
```

`items` 为 1 到 100 项；每项包含唯一 `client_id`、可选 `remark` 和一份完整 parameters。每项都必须
独立满足 Factor Schema 和 MCP/Web 托管股票池契约。提交时不会预占版本号；成功项完成输出校验和
指标收集后才自动保存为下一个
未使用的递增版本，失败项不占号。并发项按成功保存顺序编号，不保证与 `items` 顺序一致。
`client_id` 用于同一次客户端提交的幂等识别，重试应复用原值；分别轮询返回的每个
`workspace_id`。批量工具不是浏览器本地队列，调用后会立即提交。

同一 `client_id` 已在排队或运行时只返回原 Workspace，不重复提交；提交结果不确定时，新 Attempt
先用原 job marker 对账调度器，确认未创建 Instance 后才重新提交；已有 Workflow Instance 明确失败
时，新 Attempt 使用新的 job marker 完整重跑；仅自动保存失败时只重试指标收集和版本保存，不重复
执行 Factor 任务。

## 输出

当前工作流 `SUCCESS` 后：

```text
list_workflow_outputs("factor", workflow_instance_id)
```

| 名称 | 文件 | 主要内容 |
| --- | --- | --- |
| `information_coefficient` | `factor_information_coefficients.parquet` | 各 factor × return 的 IC 与 Rank IC 时序列 |
| `group_returns` | `factor_group_returns.parquet` | 各 factor × return 的极端 N 支及等数量分组收益时序列 |
| `diagnostics` | `factor_diagnostics.parquet` | 各交易日、factor × return 的有效样本覆盖、实际分组范围与组样本摘要 |

实际列名由 `factor_columns` 与 `return_columns` 拼接生成，不能假定固定的 `ret0` 等名称。以运行请求
和 Parquet schema 为准。下载方法见 `arena://docs/overview/workflows`。

新工作流固定请求这三份轻量结果；历史 Attempt 仍按其原 `requested_outputs` 返回，不会因为缺少后来
新增的 `diagnostics` 而失效。下载这类历史 Attempt 未请求的输出时，API 返回 404 和业务码
`RESULT_NOT_REQUESTED`；已请求但实际丢失的产物属于存储故障，不使用该业务码。诊断表只包含服务端
聚合计数，不保存或下载完整 `processed_data`。

保存版本时，Backend 根据 `return_specs` 计算并持久化因子摘要指标。每个 factor × return 指标同时
显式返回 `return_kind`、`return_periods` 和 `compoundable`，避免把重叠收益的空复利指标误判为计算
失败。单期简单收益与单期对数收益
分别按各自口径生成财富路径；覆盖多个交易期的重叠收益不连续复利，因此对应累计收益、年化收益、
年化波动率、Sharpe 和最大回撤为 `null`，IC 与 Rank IC 指标仍保留。浏览器读取 Parquet 时遵循同一
契约，不能自行把重叠标签当作逐日可实现收益。

ICIR 与 Rank ICIR 均按 `IC 均值 / IC 样本标准差` 计算，并以该原始观测口径保存、展示、排序和比较。
`return_specs[return_column].periods` 不参与 ICIR 计算，网页与 API 调用方都不应对 ICIR 再做年化转换。
只有 `periods=1` 的非重叠多空收益可以无歧义地生成累计收益、按 252 个交易日折算的年化收益、
年化波动和 Sharpe。项目列表默认展示该年化收益，而不是把区间累计收益标成年度指标。

## 完整调用顺序

```text
1. 读取 factor/request、overview/dsl 和 schemas/factor
2. 校验 codes_query、dataset_query、factor_columns、return_columns、return_specs 的依赖
3. create_project("factor", title)
4. run_factor_analysis(project_id, parameters)
5. 按 Workspace 轮询；失败时读完整日志
6. SUCCESS 后列出并检查 IC、group 与 diagnostics outputs
7. 需要固化时 save_version("factor", ...)
```

MCP 不提供 Factor Workspace、Attempt、工作流实例或输出的独立删除功能。
