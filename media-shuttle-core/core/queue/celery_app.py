from __future__ import annotations

import os
import re

TASK_UPLOAD_RESULT = "core.queue.tasks.process_upload_result"


def _bool_env(name: str, default: str = "0") -> bool:
    raw = os.getenv(name, default).strip().lower()
    return raw not in {"", "0", "false", "off", "no"}


def _upload_affinity_enabled() -> bool:
    return _bool_env("MEDIA_SHUTTLE_UPLOAD_AFFINITY", "1")


def _upload_queue_prefix() -> str:
    return os.getenv("MEDIA_SHUTTLE_UPLOAD_QUEUE_KEY", "media_shuttle:task_upload")


def _normalize_owner_node(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).upper()


def _upload_queue_for_target(target: str, owner_node: str | None = None) -> str:
    suffix = (target or "RCLONE").upper()
    queue = f"{_upload_queue_prefix()}@{suffix}"
    owner = _normalize_owner_node(owner_node)
    if owner and _upload_affinity_enabled():
        return f"{queue}@{owner}"
    return queue


def route_task(name: str, args=None, kwargs=None, options=None, task=None, **_extra):
    if name != TASK_UPLOAD_RESULT:
        return None

    values = list(args or [])
    packet = values[0] if values and isinstance(values[0], dict) else {}

    target = ""
    if len(values) >= 3:
        target = str(values[2] or "")
    elif kwargs:
        target = str(kwargs.get("target", "") or "")

    queue = _upload_queue_for_target(target=target, owner_node=packet.get("owner_node"))
    return {"queue": queue, "routing_key": queue}


def build_celery_app():
    """Create Celery app with JSON-only payloads.

    Celery is optional at development time. If celery is unavailable,
    returns None so other layers can run in dry mode.
    """
    try:
        from celery import Celery
    except Exception:
        return None

    redis_url = os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0")
    app = Celery("media-shuttle-core", broker=redis_url, backend=redis_url)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_routes=(route_task,),
        timezone="UTC",
        enable_utc=True,
    )
    return app
