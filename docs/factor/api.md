# Factor API

本文件说明因子研究的项目、运行、版本、批量执行和输出 API。请求字段与示例见
`arena://docs/factor/request`，DSL 见 `arena://docs/overview/dsl`，Schema 见
`arena://schemas/factor`。通用工作流诊断见 `arena://docs/overview/workflows`。

## 项目与版本

创建 Factor 项目会同时创建当前未保存版本。未保存版本有版本号和一对一 Workspace，可重复运行；
保存后参数、成功 Instance、指标与结果固定，并自动创建下一未保存版本。

网页地址：`{ARENA_WEB_URL}/factor/projects/{project_id}`。

```text
list_projects("factor", page=1, page_size=20)
create_project("factor", title)
get_project("factor", project_id)
update_project("factor", project_id, title)
```

`get_project` 返回当前 `draft`、最新保存版本和当前工作流信息。`update_project` 只改标题，不改参数、
版本、Workspace 或结果。

## 执行一次因子分析

```text
run_factor_analysis(project_id, parameters)
```

`parameters` 必须直接是完整 `FactorAnalysisParameters`：

```json
{
  "project_id": 1,
  "parameters": {
    "codes_query": {
      "start_date": "2024-01-01",
      "end_date": "2024-12-31",
      "lookback": "PT0S",
      "codes": [],
      "factors": ["weight_000300SH"],
      "derivatives": {
        "is_hs300": {
          "type": "DIRECT",
          "op": "binary.gt",
          "fields": {"left": "weight_000300SH", "right": 0},
          "params": {}
        }
      },
      "filters": ["is_hs300"]
    },
    "dataset_query": {
      "start_date": "2024-01-01",
      "end_date": "2024-12-31",
      "lookback": "P120D",
      "codes": [],
      "factors": ["close", "circ_mv", "weight_000300SH"],
      "derivatives": {
        "is_hs300": {
          "type": "DIRECT",
          "op": "binary.gt",
          "fields": {"left": "weight_000300SH", "right": 0},
          "params": {}
        },
        "momentum_20d": {
          "type": "TS",
          "op": "unary.pct_change",
          "fields": {"col": "close"},
          "params": {"periods": 20}
        },
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
      },
      "filters": ["is_hs300"]
    },
    "factor_columns": ["momentum_20d"],
    "return_columns": ["future_1d_log_return"],
    "n_groups": 5,
    "preprocess": true,
    "market_value_column": "circ_mv"
  }
}
```

服务端严格校验请求，返回 `workspace_id` 和可能暂为空的 `workflow_instance_id`。用
`get_workspace_status(workspace_id)` 轮询当前 Attempt。

## 版本读取与保存

```text
list_versions(application="factor", project_id)
get_version(application="factor", project_id, version)
save_version(application="factor", project_id, workflow_instance_id, remark="")
update_version(application="factor", project_id, version, remark)
```

- `list_versions` 同时返回已保存版本和当前未保存版本。
- `get_version` 返回 parameters、Workspace/Instance 绑定、`saved`、`is_current`、remark 和 metrics。
- `save_version` 只能保存当前未保存版本的当前成功 Workflow Instance；不能拿历史 Instance 覆盖版本。
- 保存成功后自动产生下一个未保存版本。
- `update_version` 只修改显示备注，不修改参数和结果。

## 批量执行

```text
run_factor_batch(project_id, items)
```

`items` 为 1 到 100 项；每项包含唯一 `client_id`、可选 `remark` 和一份完整 parameters：

```json
{
  "project_id": 1,
  "items": [
    {
      "client_id": "factor-grid-001",
      "remark": "5 groups",
      "parameters": {"codes_query": null, "dataset_query": {}, "factor_columns": [], "return_columns": []}
    }
  ]
}
```

示例中的空对象/数组只展示外形，不能直接执行；每项都必须独立满足 Factor Schema。成功项会自动保存
为递增版本。`client_id` 用于同一次客户端提交的幂等识别，重试应复用原值；分别轮询返回的每个
`workspace_id`。批量工具不是浏览器本地队列，调用后会立即提交。

## 输出

当前工作流 `SUCCESS` 后：

```text
list_workflow_outputs("factor", workflow_instance_id)
```

| 名称 | 文件 | 主要内容 |
| --- | --- | --- |
| `information_coefficient` | `factor_information_coefficients.parquet` | 各 factor × return 的 IC 与 Rank IC 时序列 |
| `group_returns` | `factor_group_returns.parquet` | 各 factor × return × group 的分组收益时序列 |

实际列名由 `factor_columns` 与 `return_columns` 拼接生成，不能假定固定的 `ret0` 等名称。以运行请求
和 Parquet schema 为准。下载方法见 `arena://docs/overview/workflows`。

## 完整调用顺序

```text
1. 读取 factor/request、overview/dsl 和 schemas/factor
2. 校验 codes_query、dataset_query、factor_columns、return_columns 的依赖
3. create_project("factor", title)
4. run_factor_analysis(project_id, parameters)
5. 按 Workspace 轮询；失败时读完整日志
6. SUCCESS 后列出并检查 IC/group outputs
7. 需要固化时 save_version("factor", ...)
```

MCP 不提供 Factor 项目、版本、Workspace、Attempt 或输出删除功能。
