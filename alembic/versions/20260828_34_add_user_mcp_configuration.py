"""Add user-scoped MCP prompts and deletion permissions.

Revision ID: 20260828_34
Revises: 20260828_33
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260828_34"
down_revision: str | Sequence[str] | None = "20260828_33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "mcp_custom_prompt",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
            comment="注入 Arena MCP 总览的用户自定义提示词",
        ),
    )
    for name, comment in (
        ("mcp_allow_delete_query_projects", "是否允许 MCP 删除数据查询项目"),
        ("mcp_allow_delete_factor_projects", "是否允许 MCP 删除因子分析项目"),
        ("mcp_allow_delete_backtest_projects", "是否允许 MCP 删除策略回测项目"),
        ("mcp_allow_delete_factor_versions", "是否允许 MCP 删除因子分析版本"),
        ("mcp_allow_delete_backtest_versions", "是否允许 MCP 删除策略回测版本"),
        ("mcp_allow_delete_fee_analyses", "是否允许 MCP 删除手续费分析"),
        ("mcp_allow_delete_sensitivity_analyses", "是否允许 MCP 删除参数敏感性分析"),
        ("mcp_allow_delete_optimizations", "是否允许 MCP 删除参数调优报告"),
    ):
        op.add_column(
            "users",
            sa.Column(
                name,
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
                comment=comment,
            ),
        )


def downgrade() -> None:
    for name in (
        "mcp_allow_delete_optimizations",
        "mcp_allow_delete_sensitivity_analyses",
        "mcp_allow_delete_fee_analyses",
        "mcp_allow_delete_backtest_versions",
        "mcp_allow_delete_factor_versions",
        "mcp_allow_delete_backtest_projects",
        "mcp_allow_delete_factor_projects",
        "mcp_allow_delete_query_projects",
        "mcp_custom_prompt",
    ):
        op.drop_column("users", name)
