from __future__ import annotations

from .container import build_container
from .models import CreateTaskRequest

container = build_container()

try:
    from fastapi import FastAPI, HTTPException, Query
except Exception as exc:  # pragma: no cover
    raise RuntimeError("fastapi is required to run media-shuttle-api") from exc

app = FastAPI(title="media-shuttle-api", version="1.0.0")


@app.post("/v1/tasks/parse", status_code=202)
def create_parse_task(body: dict):
    try:
        request = CreateTaskRequest(**body)
        record = container.service.create_parse_task(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"task_id": record.task_id, "status": "QUEUED"}


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str):
    record = container.service.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="task not found")
    return record.__dict__


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
