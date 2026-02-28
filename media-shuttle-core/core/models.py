from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from .enums import TaskStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TaskPayload:
    url: str
    requester_id: str
    target: str
    destination: str


@dataclass
class ParsedSource:
    site: str
    page_url: str
    download_url: str
    file_name: str
    remote_folder: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DownloadResult:
    site: str
    source_url: str
    local_path: str
    size_bytes: int
    file_name: str
    remote_folder: str | None = None


@dataclass
class UploadResult:
    location: str


@dataclass
class TaskRecord:
    task_id: str
    idempotency_key: str
    payload: TaskPayload
    status: TaskStatus = TaskStatus.QUEUED
    message: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    last_error: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data


@dataclass
class TaskCreatedEvent:
    spec_version: str
    task_id: str
    task_type: str
    idempotency_key: str
    payload: TaskPayload
    created_at: str


@dataclass
class TaskStatusEvent:
    spec_version: str
    task_id: str
    status: str
    updated_at: str
    message: str = ""
