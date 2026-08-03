"""Result file access shared by query, factor, and backtest."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session


class ResultFile(BaseModel):
    name: str
    filename: str
    size: int
    modified_at: datetime


def owned_result_task(session: Session, user_id: int, task_id: int, model: type[Any]) -> Any:
    task = session.scalar(select(model).where(model.user_id == user_id, model.task_id == task_id))
    if task is None:
        raise FileNotFoundError(f"任务不存在: {task_id}")
    return task


def require_success(task: Any) -> None:
    if task.state != "SUCCESS":
        raise RuntimeError(f"任务 {task.task_id} 当前状态为 {task.state}，成功后才能获取结果")


def result_files(session: Session, user_id: int, task_id: int, model: type[Any], output_files: dict[str, str]) -> list[dict[str, Any]]:
    task = owned_result_task(session, user_id, task_id, model)
    require_success(task)
    if not task.output_dir:
        raise OSError(f"任务 {task_id} 成功但未记录结果目录")
    output_dir = Path(task.output_dir).resolve()
    files = []
    for name in task.requested_outputs:
        filename = output_files.get(name)
        if filename is None:
            raise OSError(f"任务 {task_id} 包含未知结果: {name}")
        path = (output_dir / filename).resolve()
        if path.parent != output_dir or not path.is_file():
            raise OSError(f"任务 {task_id} 成功但缺少结果: {name}")
        stat = path.stat()
        files.append({"name": name, "filename": path.name, "size": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC)})
    return files


def result_path(session: Session, user_id: int, task_id: int, name: str, model: type[Any], output_files: dict[str, str]) -> Path:
    task = owned_result_task(session, user_id, task_id, model)
    if name not in task.requested_outputs or name not in output_files:
        raise FileNotFoundError(f"任务未请求结果: {name}")
    require_success(task)
    if not task.output_dir:
        raise OSError(f"任务 {task_id} 成功但未记录结果目录")
    output_dir = Path(task.output_dir).resolve()
    path = (output_dir / output_files[name]).resolve()
    if path.parent != output_dir or not path.is_file():
        raise OSError(f"任务 {task_id} 成功但缺少结果: {name}")
    return path
