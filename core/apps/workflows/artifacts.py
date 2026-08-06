"""Workflow workspace identity and storage path resolution."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

from config import ArenaSettings

WORKSPACE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")
WORKSPACE_APPLICATIONS = frozenset({"query", "factor", "backtest", "incremental"})
LOCAL_ONLY_APPLICATIONS = frozenset({"incremental"})


def new_workspace_key() -> str:
    return uuid4().hex


def validate_workspace_key(value: str) -> str:
    normalized = value.strip().lower()
    if not WORKSPACE_KEY_PATTERN.fullmatch(normalized):
        raise ValueError(f"无效的 workspace key: {value}")
    return normalized


def validate_workspace_application(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in WORKSPACE_APPLICATIONS:
        raise ValueError(f"无效的 workspace application: {value}")
    return normalized


def workspace_directory(application: str, workspace_key: str) -> Path:
    normalized_application = validate_workspace_application(application)
    key = validate_workspace_key(workspace_key)
    root = ArenaSettings.SHARED_DIR.resolve()
    application_directory = root / normalized_application
    candidate = application_directory / key
    if application_directory.is_symlink() or candidate.is_symlink():
        raise ValueError(f"workspace 路径不能是符号链接: {candidate}")
    application_directory = application_directory.resolve()
    directory = candidate.resolve()
    if application_directory.parent != root:
        raise ValueError(f"workspace application 路径越界: {application_directory}")
    if directory.parent != application_directory:
        raise ValueError(f"workspace 路径越界: {directory}")
    return directory


def workspace_input_file(application: str, workspace_key: str) -> Path:
    return workspace_directory(application, workspace_key) / "input.json"


def workspace_output_directory(application: str, workspace_key: str) -> Path:
    return workspace_directory(application, workspace_key) / "output"


def workspace_output_prefix(application: str, workspace_key: str) -> str:
    normalized_application = validate_workspace_application(application)
    key = validate_workspace_key(workspace_key)
    return f"{normalized_application}/{key}/output"


def uses_cloud_output(application: str) -> bool:
    return ArenaSettings.SHARED_CLOUD and application not in LOCAL_ONLY_APPLICATIONS


def runtime_output_argument(application: str, workspace_key: str) -> str:
    if uses_cloud_output(application):
        return workspace_output_prefix(application, workspace_key)
    return str(workspace_output_directory(application, workspace_key))
