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
    # `destination` is optional. If omitted (or empty), the server fills in
    # the default for the target. For RCLONE the default is ``"115:/"``
    # which, combined with the worker's ``MEDIA_SHUTTLE_USE_DATE_CATEGORY=1``
    # and the per-source ``remote_folder`` set by the parsers, produces a
    # final path of the form ``115:/<date>/<folder>/<file>``. For
    # TELEGRAM the caller must still supply a destination (no sensible
    # default).
    destination: str = ""


@dataclass
class CreateForumTaskRequest:
    """Body of ``POST /v1/tasks/parse_forum``.

    Same shape as :class:`CreateTaskRequest` plus an optional
    ``max_pages`` override. The forum dispatcher reads the
    rest of the payload (target / destination) identically
    to a regular parse_link task — the fan-out events it
    emits downstream are vanilla ``parse_link`` events
    carrying the same target / destination.

    ``max_pages`` is capped at the runtime ``FORUM_MAX_PAGES``
    floor (the request can only lower the cap, not raise
    it). The cap is intentionally not operator-tunable per
    request: the fan-out cap (default 200) is a safety
    threshold, and exposing it would let a misclick blow
    up the queue.
    """
    url: str
    requester_id: str
    target: str = "RCLONE"
    destination: str = ""
    max_pages: int = 0


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


@dataclass
class WorkerRecord:
    hostname: str
    role: str = ""
    queue: str = ""
    queues: list[str] = field(default_factory=list)
    status: str = "UNKNOWN"
    concurrency: int = 1
    desired_concurrency: int = 1
    node_id: str = ""
    pid: int | None = None
    exit_code: int | None = None
    rate_limits: dict[str, str] = field(default_factory=dict)
    last_error: str = ""
    started_at: str = field(default_factory=utc_now_iso)
    last_heartbeat_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
