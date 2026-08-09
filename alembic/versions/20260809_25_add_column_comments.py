"""Add comments to every database column.

Revision ID: 20260809_25
Revises: 20260809_24
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260809_25"
down_revision: str | None = "20260809_24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMN_COMMENTS = {
    "alembic_version_backend": {"version_num": "后端数据库迁移版本"},
    "users": {
        "id": "用户主键",
        "username": "登录用户名",
        "password_hash": "密码哈希",
        "is_admin": "是否为管理员",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "query_projects": {
        "id": "查询项目主键",
        "user_id": "所属用户主键",
        "title": "项目名称",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "factor_projects": {
        "id": "因子研究项目主键",
        "user_id": "所属用户主键",
        "title": "项目名称",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "backtest_projects": {
        "id": "策略回测项目主键",
        "user_id": "所属用户主键",
        "title": "项目名称",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "workflow_runs": {
        "id": "工作流运行主键",
        "user_id": "提交用户主键",
        "application": "所属应用类型",
        "workspace_key": "运行工作空间唯一标识",
        "source_project_id": "来源应用项目主键",
        "submission_state": "工作流提交状态",
        "project_code": "DolphinScheduler 项目编码",
        "workflow_definition_code": "DolphinScheduler 工作流定义编码",
        "workflow_name": "工作流定义名称",
        "payload": "当前运行请求及启动参数",
        "requested_outputs": "请求生成的输出名称",
        "error": "运行或提交错误信息",
        "events": "工作流业务事件记录",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "query_workflow_runs": {
        "id": "工作流运行主键",
        "project_id": "关联查询项目主键",
    },
    "factor_workflow_runs": {
        "id": "工作流运行主键",
        "project_id": "关联因子项目主键",
        "saved": "是否已保存为版本",
    },
    "backtest_workflow_runs": {
        "id": "工作流运行主键",
        "project_id": "关联回测项目主键",
        "saved": "是否已保存为版本",
    },
    "incremental_workflow_runs": {"id": "工作流运行主键"},
    "workflow_instances": {
        "workflow_instance_id": "DolphinScheduler 工作流实例主键",
        "workflow_run_id": "所属工作流运行主键",
        "state": "工作流实例状态",
        "is_current": "是否为当前重试实例",
        "started_at": "开始执行时间",
        "finished_at": "结束执行时间",
        "duration_seconds": "执行耗时秒数",
        "last_synced_at": "最近同步调度器时间",
        "error": "实例执行错误信息",
        "state_history": "实例状态变更历史",
        "payload_snapshot": "该实例的运行请求快照",
        "requested_outputs_snapshot": "该实例的输出请求快照",
        "created_at": "创建时间",
        "updated_at": "更新时间",
    },
    "factor_versions": {
        "id": "因子版本主键",
        "project_id": "所属因子项目主键",
        "workflow_instance_id": "生成该版本的工作流实例主键",
        "version": "项目内递增版本号",
        "remark": "版本备注",
        "parameters": "因子分析请求参数快照",
        "metrics": "因子分析摘要指标",
        "created_at": "创建时间",
    },
    "backtest_versions": {
        "id": "回测版本主键",
        "project_id": "所属回测项目主键",
        "workflow_instance_id": "生成该版本的工作流实例主键",
        "version": "项目内递增版本号",
        "remark": "版本备注",
        "parameters": "回测请求参数快照",
        "summary": "回测摘要指标",
        "created_at": "创建时间",
    },
    "backtest_researches": {
        "id": "批量研究主键",
        "version_id": "来源回测版本主键",
        "analysis_type": "批量研究类型",
        "description": "批量研究备注",
        "created_at": "创建时间",
    },
    "backtest_research_runs": {
        "id": "批量研究运行主键",
        "research_id": "所属批量研究主键",
        "workflow_run_id": "关联回测工作流运行主键",
        "parameter_overrides": "相对来源版本的参数变更",
        "result_workflow_instance_id": "生成当前指标的工作流实例主键",
        "metrics": "批量研究运行结果指标",
        "result_error": "结果指标生成错误",
    },
}


def upgrade() -> None:
    for table, columns in COLUMN_COMMENTS.items():
        for column, comment in columns.items():
            op.alter_column(table, column, comment=comment)


def downgrade() -> None:
    for table, columns in reversed(COLUMN_COMMENTS.items()):
        for column in columns:
            op.alter_column(table, column, comment=None)
