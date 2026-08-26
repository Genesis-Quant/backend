"""Shared project-list search and ordering helpers."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

Project = TypeVar("Project")
ProjectSummary = dict[str, Any]


def list_project_summaries(
    session: Session,
    base_statement: Any,
    project_model: Any,
    summarize: Callable[[Session, Sequence[Project]], list[ProjectSummary]],
    page: int,
    page_size: int,
    search: str | None,
    sort_by: str,
    sort_order: str,
    database_sort_columns: Mapping[str, Any],
    in_memory_sort_values: Mapping[str, Callable[[ProjectSummary], Any]],
) -> dict[str, Any]:
    """Search, order, and paginate project rows before returning summaries."""
    statement = apply_project_search(base_statement, project_model, search)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    all_total = total
    if (search or "").strip():
        all_total = session.scalar(select(func.count()).select_from(base_statement.subquery())) or 0

    if sort_by in database_sort_columns:
        column = database_sort_columns[sort_by]
        order = (
            column.desc().nulls_last()
            if sort_order == "desc"
            else column.asc().nulls_last()
        )
        projects = session.scalars(
            statement.order_by(order, project_model.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = summarize(session, projects)
    else:
        items = summarize(session, session.scalars(statement).all())
        items = sort_project_summaries(
            items,
            in_memory_sort_values[sort_by],
            sort_order == "desc",
        )[(page - 1) * page_size:page * page_size]

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "all_total": all_total,
    }


def apply_project_search(statement: Any, project_model: Any, search: str | None) -> Any:
    """Filter a project statement by a literal title or ID fragment."""
    term = (search or "").strip()
    if not term:
        return statement
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return statement.where(
        or_(
            project_model.title.ilike(pattern, escape="\\"),
            cast(project_model.id, String).like(pattern, escape="\\"),
        )
    )


def sort_project_summaries(
    items: list[dict[str, Any]],
    value: Callable[[dict[str, Any]], Any],
    descending: bool,
) -> list[dict[str, Any]]:
    """Sort computed project fields stably while keeping missing values last."""
    present = [item for item in items if value(item) is not None]
    missing = [item for item in items if value(item) is None]
    present.sort(key=lambda item: item["id"], reverse=True)
    present.sort(key=lambda item: sortable_value(value(item)), reverse=descending)
    missing.sort(key=lambda item: item["id"], reverse=True)
    return present + missing


def sortable_value(value: Any) -> Any:
    return value.casefold() if isinstance(value, str) else value
