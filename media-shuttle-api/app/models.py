from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone



def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class CreateTaskRequest:
    url: str
    requester_id: str
    target: str
    destination: str


@dataclass
class TaskRecord:
    task_id: str
    idempotency_key: str
    status: str
    requester_id: str
    url: str
    target: str
    destination: str
    message: str = ""
    sources: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    last_error: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class TaskCreatedEvent:
    spec_version: str
    task_id: str
    task_type: str
    idempotency_key: str
    created_at: str
    payload: dict
