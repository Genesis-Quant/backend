"""Fixed document catalog exposed by the Arena MCP page."""

from dataclasses import dataclass

from config import MCPSettings
from core.mcp.schemas import DocumentName
from core.mcp.services import document

from .schemas import McpCatalog, McpDocument, McpDocumentSummary, McpSection


@dataclass(frozen=True, slots=True)
class DocumentDefinition:
    slug: DocumentName
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class SectionDefinition:
    slug: str
    title: str
    description: str
    items: tuple[DocumentDefinition, ...]


MCP_DOCUMENT_SECTIONS = (
    SectionDefinition(
        slug="overview",
        title="开始使用",
        description="连接、对象关系、查询语言与运行生命周期。",
        items=(
            DocumentDefinition("overview/overview", "Arena MCP 总览", "认证、返回值、文档导航与通用执行流程。"),
            DocumentDefinition("overview/projects", "Arena 对象关系", "Project、Version、Workspace、Attempt、Workflow 与 Task 的关系。"),
            DocumentDefinition("overview/dsl", "Factor Query DSL", "字段、派生节点、依赖、过滤器与时序边界。"),
            DocumentDefinition("overview/workflows", "工作流与任务读取", "运行历史、任务、日志分页、输出下载与控制。"),
            DocumentDefinition("overview/dolphindb", "DolphinDB 脚本执行", "认证用户只读脚本测试接口、权限与计算资源边界。"),
        ),
    ),
    SectionDefinition(
        slug="query",
        title="数据查询",
        description="查询请求、项目运行与结果读取。",
        items=(
            DocumentDefinition("query/request", "Query 请求契约", "FactorQuery 字段、执行顺序、范围与提交前检查。"),
            DocumentDefinition("query/api", "Query API", "Query 项目、运行、历史参数、输出与网页入口。"),
        ),
    ),
    SectionDefinition(
        slug="factor",
        title="因子分析",
        description="分析输入、版本管理与输出读取。",
        items=(
            DocumentDefinition("factor/request", "Factor 请求契约", "两阶段查询、分析列、预处理与提交前检查。"),
            DocumentDefinition("factor/api", "Factor API", "Factor 项目、运行、版本、批量执行与输出。"),
        ),
    ),
    SectionDefinition(
        slug="backtest",
        title="回测",
        description="回测输入、数据时点、运行边界与结果核验。",
        items=(
            DocumentDefinition("backtest/request", "Backtest 请求契约", "查询、配置、回调、输出要求与提交前检查。"),
            DocumentDefinition("backtest/api", "Backtest API", "Backtest 项目、运行、版本、批量执行与输出。"),
            DocumentDefinition("backtest/dolphindb", "DolphinDB Backtest 运行契约", "价格尺度、合成快照、撮合、订单簿、持仓与资金。"),
            DocumentDefinition("backtest/dynamic-pool", "动态数据域与时点契约", "候选并集、逐日状态、退出保留与 point-in-time 边界。"),
            DocumentDefinition("backtest/optimization", "二次规划与目标权重契约", "求解接口、矩阵维度、状态、数值容差与调仓顺序。"),
            DocumentDefinition("backtest/callback-data", "回调与交易对象契约", "回调消息、持仓、订单和成交对象的读取规则。"),
            DocumentDefinition("backtest/results", "Backtest 结果与审计契约", "结果表粒度、订单状态、费用、关联字段与已知限制。"),
            DocumentDefinition("backtest/qa", "回测端到端与结果 QA", "运行生命周期、结果对账、指标口径与保存顺序。"),
        ),
    ),
)

MCP_DOCUMENT_INDEX = {
    item.slug: (section.slug, item)
    for section in MCP_DOCUMENT_SECTIONS
    for item in section.items
}


def mcp_catalog() -> McpCatalog:
    sections = tuple(
        McpSection(
            slug=section.slug,
            title=section.title,
            description=section.description,
            items=tuple(
                McpDocumentSummary(
                    slug=item.slug,
                    title=item.title,
                    description=item.description,
                )
                for item in section.items
            ),
        )
        for section in MCP_DOCUMENT_SECTIONS
    )
    return McpCatalog(
        mcp_url=MCPSettings.ENDPOINT_URL,
        sections=sections,
        total=len(MCP_DOCUMENT_INDEX),
    )


def mcp_document(slug: str) -> McpDocument:
    registered = MCP_DOCUMENT_INDEX.get(slug)
    if registered is None:
        raise FileNotFoundError(f"Arena 文档不存在：{slug}")
    section, item = registered
    return McpDocument(
        slug=item.slug,
        title=item.title,
        description=item.description,
        section=section,
        content=document(item.slug),
    )


__all__ = [
    "MCP_DOCUMENT_INDEX",
    "MCP_DOCUMENT_SECTIONS",
    "mcp_catalog",
    "mcp_document",
]
