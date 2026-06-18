from __future__ import annotations

import time

from .container import build_container
from .logging import setup_logging
from .models import CreateTaskRequest, CreateForumTaskRequest

logger = setup_logging()
container = build_container()

try:
    from fastapi import FastAPI, HTTPException, Query
except Exception as exc:  # pragma: no cover
    raise RuntimeError("fastapi is required to run media-shuttle-api") from exc

app = FastAPI(title="media-shuttle-api", version="1.0.0")


@app.middleware("http")
async def log_requests(request, call_next):
    started_at = time.perf_counter()
    client_ip = request.client.host if request.client is not None else "-"
    method = request.method
    path = request.url.path

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.exception(
            "api request failed method={} path={} client_ip={} duration_ms={}",
            method,
            path,
            client_ip,
            duration_ms,
        )
        raise

    duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    logger.info(
        "api request method={} path={} status={} client_ip={} duration_ms={}",
        method,
        path,
        response.status_code,
        client_ip,
        duration_ms,
    )
    return response


@app.post("/v1/tasks/parse", status_code=202)
def create_parse_task(body: dict):
    try:
        request = CreateTaskRequest(**body)
        record = container.service.create_parse_task(request)
    except (ValueError, TypeError) as exc:
        # TypeError covers malformed bodies that omit required fields
        # (e.g. ``{}``); ValueError covers semantic errors raised by
        # ``validate_create_request``.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task_id": record.task_id, "status": "QUEUED"}


@app.post("/v1/tasks/parse_forum", status_code=202)
def create_forum_thread_task(body: dict):
    """Submit a forum thread for link extraction + fan-out.

    Body shape: see :class:`CreateForumTaskRequest`. The
    forum dispatcher is async (B-scheme from the design
    notes) so the response returns a ``task_id`` and the
    actual extraction happens in a background worker.
    """
    try:
        request = CreateForumTaskRequest(**body)
        record = container.service.create_forum_thread_task(request)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task_id": record.task_id, "status": "QUEUED"}


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str):
    record = container.service.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="task not found")
    return record.__dict__


@app.post("/v1/tasks/{task_id}/retry", status_code=202)
def retry_task(task_id: str, body: dict | None = None):
    """Re-queue a failed task for the operator who owns it.

    Backs the inline ``🔁 重试`` button on the Telegram
    failure notification. ``requester_id`` is required —
    it must match the requester stored on the original
    task record, otherwise we return 404 (the same
    response we'd give for a non-existent task_id, so we
    do not leak whether the task exists under another
    owner). ``phase`` is accepted for forward
    compatibility (the inline button eventually wants to
    retry just the download or just the upload) but the
    current implementation always re-runs the whole
    pipeline: the original local download may have been
    cleaned up by the time the operator clicks the
    button, and a partial re-run would have to recreate
    the parser's source list from cache, which the
    pipeline does not expose yet.
    """
    payload = body or {}
    requester_id = str(payload.get("requester_id", "")).strip()
    if not requester_id:
        # 403 is a clearer signal than 404 for a missing
        # operator id; the button click always supplies one
        # (it comes from the original task.completed event
        # that rendered the button), so a missing value
        # here means a misconfigured client.
        raise HTTPException(status_code=403, detail="requester_id required")
    result = container.service.admin_retry_action(
        mode="failed",
        task_id=task_id,
        requester_id=requester_id,
    )
    if not result.get("accepted"):
        reason = result.get("reason", "unknown")
        if reason == "task_not_found":
            # Covers both the genuine 404 and the
            # cross-operator mismatch: deliberately
            # indistinguishable so we don't leak the
            # existence of someone else's task.
            raise HTTPException(status_code=404, detail="task not found")
        if reason == "task_not_failed":
            # Already retried / already running. 409 lets
            # the bot show a "已重试过" message instead of
            # an opaque 404.
            raise HTTPException(status_code=409, detail="task not in FAILED state")
        raise HTTPException(status_code=400, detail=reason)
    return result


@app.get("/v1/tasks")
def list_tasks(status: str | None = None, limit: int = Query(default=20, ge=1, le=100)):
    items = container.service.list_tasks(status=status, limit=limit)
    return {"items": [item.__dict__ for item in items], "total": len(items)}


@app.get("/v1/stats/queue")
def queue_stats():
    return container.service.queue_stats()


@app.post("/v1/admin/workers")
def admin_workers(body: dict):
    return container.service.admin_worker_action(
        worker=body.get("worker", ""),
        queue=body.get("queue", ""),
        concurrency=int(body.get("concurrency", 1)),
        action=body.get("action", "set"),
        node_id=body.get("node_id", ""),
        role=body.get("role", ""),
    )


@app.get("/v1/admin/workers")
def list_workers(status: str | None = None, limit: int = Query(default=100, ge=1, le=500), refresh: bool = True):
    items = container.service.list_workers(status=status, limit=limit, refresh=refresh)
    return {"items": [item.__dict__ for item in items], "total": len(items)}


@app.post("/v1/admin/rate-limit")
def admin_rate_limit(body: dict):
    return container.service.admin_rate_limit_action(
        worker=body.get("worker", ""),
        task_type=body.get("task_type", ""),
        rate_limit=body.get("rate_limit", ""),
    )


@app.post("/v1/admin/retry")
def admin_retry(body: dict):
    return container.service.admin_retry_action(
        mode=body.get("mode", "failed"),
        task_id=body.get("task_id"),
        limit=int(body.get("limit", 20)),
    )


@app.post("/v1/admin/settings")
def admin_settings(body: dict):
    return container.service.admin_setting_action(key=body.get("key", ""), value=body.get("value", ""))


@app.post("/v1/admin/cleanup-downloads")
def admin_cleanup_downloads(body: dict | None = None):
    """Manually wipe the local download directory.

    Backed by ``sweep_download_dir`` which only touches paths
    inside the resolved ``MEDIA_SHUTTLE_DOWNLOAD_DIR``. Use
    ``{"dry_run": true}`` to preview what would be removed
    without actually deleting anything.
    """
    payload = body or {}
    dry_run_raw = payload.get("dry_run", False)
    if isinstance(dry_run_raw, str):
        dry_run = dry_run_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        dry_run = bool(dry_run_raw)
    return container.service.admin_cleanup_downloads_action(dry_run=dry_run)
