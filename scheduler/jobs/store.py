"""Durable Arena job metadata stored beside shared task inputs."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, cast
from uuid import UUID, uuid4

from scheduler.domain import (
    APPLICATION_OUTPUTS,
    APPLICATIONS,
    JOB_KINDS,
    ApplicationName,
    JobKind,
)
from scheduler.errors import JobValidationError

_store_lock = RLock()


class SharedJobStore:
    """Persist one scheduler job below the configured shared mount."""

    def __init__(self, shared_dir: Path) -> None:
        self.shared_dir = shared_dir.resolve()

    def create(
        self,
        application: ApplicationName,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Create an application job with isolated input and output paths."""
        self._validate_payload(application, payload)
        metadata = self._new_metadata(application)
        job_dir = self._job_dir(application, metadata["job_id"])
        output_dir = job_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=False)

        runtime_input = deepcopy(payload)
        requested_outputs = runtime_input.pop("output")
        runtime_input["output_dir"] = "output"
        input_file = job_dir / "input.json"
        self._write_json(input_file, runtime_input)
        metadata.update(
            {
                "input_file": str(input_file),
                "output_dir": str(output_dir),
                "requested_outputs": requested_outputs,
            }
        )
        self.save(metadata)
        return metadata

    def create_workflow_job(self, workflow: JobKind) -> dict[str, Any]:
        """Create metadata for a workflow that needs no Runtime input file."""
        if workflow not in JOB_KINDS:
            raise JobValidationError(f"不支持的任务类型: {workflow}")
        metadata = self._new_metadata(workflow)
        self._job_dir(workflow, metadata["job_id"]).mkdir(
            parents=True,
            exist_ok=False,
        )
        self.save(metadata)
        return metadata

    def load(self, job_id: str) -> dict[str, Any]:
        normalized_id = self._normalize_job_id(job_id)
        with _store_lock:
            matches = list(self.shared_dir.glob(f"*/{normalized_id}/job.json"))
            if len(matches) != 1:
                raise FileNotFoundError(f"任务不存在: {job_id}")
            return json.loads(matches[0].read_text(encoding="utf-8"))

    def list(
        self,
        *,
        application: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if application is not None and application not in JOB_KINDS:
            raise JobValidationError(f"不支持的任务类型: {application}")
        if limit < 1 or limit > 1000:
            raise JobValidationError("limit 必须在 1 到 1000 之间")
        pattern = (
            f"{application}/*/job.json"
            if application is not None
            else "*/*/job.json"
        )
        jobs = []
        with _store_lock:
            for path in self.shared_dir.glob(pattern):
                try:
                    metadata = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if state is not None and metadata.get("state") != state:
                    continue
                jobs.append(metadata)
        jobs.sort(key=lambda job: job.get("created_at", ""), reverse=True)
        return jobs[:limit]

    def save(self, metadata: dict[str, Any]) -> None:
        application = cast(JobKind, metadata["application"])
        job_id = self._normalize_job_id(str(metadata["job_id"]))
        job_dir = self._job_dir(application, job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        metadata["updated_at"] = datetime.now(UTC).isoformat()
        with _store_lock:
            self._write_json(job_dir / "job.json", metadata)

    def append_event(
        self,
        metadata: dict[str, Any],
        event: str,
        **details: Any,
    ) -> None:
        metadata.setdefault("events", []).append(
            {
                "event": event,
                "timestamp": datetime.now(UTC).isoformat(),
                **details,
            }
        )
        self.save(metadata)

    def response(self, metadata: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(metadata)
        output_dir_value = metadata.get("output_dir")
        outputs = []
        if output_dir_value:
            output_dir = Path(output_dir_value)
            if output_dir.is_dir():
                outputs = [
                    {
                        "name": output.name,
                        "path": str(output),
                        "size": output.stat().st_size,
                        "modified_at": datetime.fromtimestamp(
                            output.stat().st_mtime,
                            UTC,
                        ).isoformat(),
                    }
                    for output in sorted(output_dir.glob("*.parquet"))
                ]
        result["outputs"] = outputs
        return result

    @staticmethod
    def _new_metadata(application: JobKind) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        job_id = uuid4().hex
        return {
            "job_id": job_id,
            "application": application,
            "state": "CREATED",
            "created_at": now,
            "updated_at": now,
            "input_file": None,
            "output_dir": None,
            "project_code": None,
            "workflow_name": None,
            "process_definition_code": None,
            "process_instance_id": None,
            "process_instance_history": [],
            "scheduler_submission": None,
            "scheduler_state": None,
            "last_action": None,
            "error": None,
            "events": [
                {
                    "event": "CREATED",
                    "timestamp": now,
                }
            ],
        }

    @staticmethod
    def _validate_payload(
        application: ApplicationName,
        payload: dict[str, Any],
    ) -> None:
        if application not in APPLICATIONS:
            raise JobValidationError(f"不支持的应用: {application}")
        if "output_dir" in payload:
            raise JobValidationError(
                "output_dir 由 backend 统一设置为共享任务目录，请勿传入"
            )
        required = {"dataset_query", "output"}
        if application == "factor":
            required.update(("factor_columns", "return_columns"))
        if application == "backtest":
            required.add("callbacks")
        missing = sorted(required - payload.keys())
        if missing:
            raise JobValidationError(f"缺少必填字段: {missing}")
        outputs = payload["output"]
        if not isinstance(outputs, list) or not outputs or not all(isinstance(output, str) for output in outputs):
            raise JobValidationError("output 必须是非空字符串数组")
        if len(outputs) != len(set(outputs)):
            raise JobValidationError("output 中的名称不能重复")
        if unsupported := sorted(set(outputs) - set(APPLICATION_OUTPUTS[application])):
            raise JobValidationError(f"{application} 不支持以下输出: {unsupported}")

    def _job_dir(self, application: JobKind, job_id: str) -> Path:
        if application not in JOB_KINDS:
            raise JobValidationError(f"不支持的任务类型: {application}")
        return self.shared_dir / application / self._normalize_job_id(job_id)

    @staticmethod
    def _normalize_job_id(job_id: str) -> str:
        try:
            return UUID(job_id).hex
        except ValueError as error:
            raise FileNotFoundError(f"任务不存在: {job_id}") from error

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
