# DolphinDB 任意脚本测试接口

`execute_dolphindb_script` 用于核对部署版本中的 DolphinDB 语法、函数签名、矩阵维度、插件版本和小型
数据变换。它不是 Query、Factor 或 Backtest 工作流，也不能进入其他任务已经关闭的 DolphinDB session。

## 权限与危险边界

- 所有已认证 Arena 用户均可调用，仍按 Bearer Token 校验身份并记录 `user_id`；
- 使用 Backend 的 `DOLPHIN_RUNTIME_USERNAME` / `DOLPHIN_RUNTIME_PASSWORD` 只读运行账号连接共享实例；
- 该账号只具备脚本执行和业务数据库读取权限；持久化写入、删改、建库建表和数据库管理操作会被
  DolphinDB 权限系统拒绝；
- 脚本原样执行，没有语句白名单、计算资源沙箱或资源配额；DolphinDB session 从连接成功起最多
  使用 10 分钟，到期会关闭连接并使调用失败，但客户端超时和只读权限都不能替代服务端资源隔离；
- 每次调用创建一个新 session，执行完成或失败后关闭；不同调用不能依赖 session 局部变量；
- Backend 审计日志记录 Arena `user_id`、脚本 SHA-256 摘要、长度、成功或失败、异常类型和耗时，
  不记录脚本文本，避免日志再次泄露密钥；调用方仍不得在脚本中写入 Token、密码或其他秘密；
- `max_rows` 只截断 MCP 响应，不限制 DolphinDB 计算量，也不阻止客户端先接收完整计算结果。长时间、
  大内存或生产数据修改应使用受控运维流程，不应借此工具执行。

## 调用与返回

```text
execute_dolphindb_script(script, max_rows=200)
```

| 参数 | 约束 | 含义 |
| --- | --- | --- |
| `script` | 1 到 200000 字符，不能全为空白 | 原样执行的 DolphinScript；返回最后一个表达式 |
| `max_rows` | 1 到 2000，默认 200 | 表的行列、矩阵的行列和嵌套容器每层的响应预览上限；另受总预算限制 |

业务结果仍在 `structuredContent.result`：

```json
{
  "kind": "table",
  "python_type": "pandas.core.frame.DataFrame",
  "row_count": 3,
  "column_count": 2,
  "columns": ["name", "version"],
  "truncated": false,
  "value": [{"name": "Backtest", "version": "2.00.16.32"}]
}
```

`kind` 为 `null`、`scalar`、`table`、`vector`、`matrix`、`mapping` 或 `other`。非有限浮点转换为 JSON
`null`，时间转换为 ISO 字符串，bytes 转十六进制。`row_count`、`column_count` 是未截断的顶层维度；
表最多预览前 `max_rows` 行和列，矩阵最多预览前 `max_rows × max_rows`，嵌套容器每层也受同一上限。
单次响应还统一限制为最多 100000 个值、1000000 个字符和 32 层嵌套，避免单个超长字符串或宽表
绕过 `max_rows`。`truncated=true` 表示至少一项受行列或总预算截断。`print(...)` 进入 Backend 日志，
不进入 `value`；需要读取的对象必须放在脚本最后一个表达式。

## 基础诊断

标量：

```json
{"script":"1 + 1","max_rows":20}
```

读取当前数据库版本或函数定义时，只返回需要的元数据，不在共享实例中构造业务模型：

```json
{
  "script": "version()",
  "max_rows": 50
}
```

脚本只返回最后一个表达式；要同时返回多个对象，应显式组成字典：

```dos
result = dict(STRING, ANY)
result[`databaseVersion] = version()
result[`plugins] = getLoadedPlugins()
result
```

二次规划的通用接口、维度和结果校验见 `arena://docs/backtest/optimization`。本页不提供具体矩阵或
约束构造。

## 与工作流的关系

该工具不创建 Project、Version、Workspace、Attempt、Workflow Instance、Task 或 Parquet 输出，也不
经过 DolphinScheduler。要验证真实 Query/Factor/Backtest 必须调用对应 `run_*`，再按 Workspace
轮询。任意脚本执行成功只证明这一段脚本在当前数据库 session 中成功，不证明策略请求、数据时序或
结果 QA 正确。

普通函数签名优先使用只读的 `describe_dolphindb_functions`；只有需要验证函数组合、类型和返回结构时
才使用本工具。DolphinDB 官方函数文档入口：
[函数参考](https://docs.dolphindb.com/zh/Functions/index.html)、
[OSQP](https://docs.dolphindb.com/en/3.00.5/Functions/o/osqp.html)、
[quadprog](https://docs.dolphindb.com/zh/Functions/q/quadprog.html)。

本页示例中的 Backtest 版本与 `arena://docs/backtest/interfaces` 当前能力矩阵一致，为
`2.00.16.32`。Server、Backtest 和 MatchingEngineSimulator 的实际版本必须在目标部署中通过本工具
重新读取；只要实际 Backtest 版本与接口矩阵不同，就必须重新验证接口、返回形式和结果字段，不能只
替换文档版本号。本页不再为未实时查询的 Server 或其他插件声明“当前版本”。
