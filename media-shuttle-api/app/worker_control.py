from __future__ import annotations

from dataclasses import dataclass
import re

from .models import utc_now_iso


class WorkerControl:
    def inspect_workers(self) -> dict[str, dict]:
        raise NotImplementedError

    def add_queue(self, worker: str, queue: str) -> dict:
        raise NotImplementedError

    def set_concurrency(self, worker: str, concurrency: int) -> dict:
        raise NotImplementedError

    def shutdown(self, worker: str) -> dict:
        raise NotImplementedError

    def set_rate_limit(self, worker: str, task_name: str, rate_limit: str) -> dict:
        raise NotImplementedError

    def publish_control_command(
        self, *, node_id: str, role: str, action: str, concurrency: int = 1, queue: str = ""
    ) -> dict:
        raise NotImplementedError


class InMemoryWorkerControl(WorkerControl):
    def inspect_workers(self) -> dict[str, dict]:
        return {}

    def add_queue(self, worker: str, queue: str) -> dict:
        return {"accepted": True, "worker": worker, "queue": queue, "action": "add_queue"}

    def set_concurrency(self, worker: str, concurrency: int) -> dict:
        return {
            "accepted": True,
            "worker": worker,
            "action": "set_concurrency",
            "before": None,
            "after": max(1, int(concurrency)),
            "delta": None,
        }

    def shutdown(self, worker: str) -> dict:
        return {"accepted": True, "worker": worker, "action": "shutdown"}

    def set_rate_limit(self, worker: str, task_name: str, rate_limit: str) -> dict:
        return {
            "accepted": True,
            "worker": worker,
            "task_name": task_name,
            "rate_limit": rate_limit,
            "action": "rate_limit",
        }

    def publish_control_command(
        self, *, node_id: str, role: str, action: str, concurrency: int = 1, queue: str = ""
    ) -> dict:
        return {
            "accepted": True,
            "action": action,
            "node_id": _normalize_node(node_id),
            "role": role,
            "concurrency": max(1, int(concurrency)),
            "queue": queue,
        }


@dataclass
class CeleryWorkerControl(WorkerControl):
    redis_url: str
    celery_app: object | None = None
    control_queue_prefix: str = "media_shuttle:worker_control"
    control_task_name: str = "core.queue.tasks.apply_worker_control"

    def __post_init__(self) -> None:
        if self.celery_app is None:
            self.celery_app = _build_celery_app(self.redis_url)

    def _unsupported(self, action: str, **extra) -> dict:
        return {"accepted": False, "action": action, "reason": "celery_unavailable", **extra}

    def inspect_workers(self) -> dict[str, dict]:
        if self.celery_app is None:
            return {}
        inspect = self.celery_app.control.inspect()
        stats = inspect.stats() or {}
        active_queues = inspect.active_queues() or {}
        now = utc_now_iso()

        result: dict[str, dict] = {}
        for hostname, payload in stats.items():
            queues = []
            for item in active_queues.get(hostname, []) or []:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    queues.append(item["name"])

            result[hostname] = {
                "hostname": hostname,
                "status": "READY",
                "concurrency": _extract_concurrency(payload),
                "desired_concurrency": _extract_concurrency(payload),
                "queues": queues,
                "queue": ",".join(queues),
                "pid": payload.get("pid"),
                "last_heartbeat_at": now,
                "updated_at": now,
            }
        return result

    def add_queue(self, worker: str, queue: str) -> dict:
        if self.celery_app is None:
            return self._unsupported("add_queue", worker=worker, queue=queue)
        self.celery_app.control.add_consumer(queue=queue, destination=[worker], reply=False)
        return {"accepted": True, "worker": worker, "queue": queue, "action": "add_queue"}

    def set_concurrency(self, worker: str, concurrency: int) -> dict:
        if self.celery_app is None:
            return self._unsupported("set_concurrency", worker=worker, concurrency=concurrency)

        requested = max(1, int(concurrency))
        inspect = self.celery_app.control.inspect(destination=[worker])
        stats = inspect.stats() or {}
        payload = stats.get(worker)
        current = _extract_concurrency(payload) if isinstance(payload, dict) else None
        if current is None:
            return {"accepted": False, "worker": worker, "action": "set_concurrency", "reason": "worker_not_found"}

        pool_impl = _extract_pool_impl(payload)
        if "solo" in pool_impl.lower() and requested != current:
            return {
                "accepted": False,
                "worker": worker,
                "action": "set_concurrency",
                "reason": "pool_resize_not_supported_for_solo",
                "before": current,
                "after": current,
                "requested": requested,
            }

        delta = requested - current
        if delta > 0:
            self.celery_app.control.pool_grow(n=delta, destination=[worker], reply=False)
        elif delta < 0:
            self.celery_app.control.pool_shrink(n=abs(delta), destination=[worker], reply=False)
        return {
            "accepted": True,
            "worker": worker,
            "action": "set_concurrency",
            "before": current,
            "after": requested,
            "delta": delta,
        }

    def shutdown(self, worker: str) -> dict:
        if self.celery_app is None:
            return self._unsupported("shutdown", worker=worker)
        self.celery_app.control.broadcast("shutdown", destination=[worker], reply=False)
        return {"accepted": True, "worker": worker, "action": "shutdown"}

    def set_rate_limit(self, worker: str, task_name: str, rate_limit: str) -> dict:
        if self.celery_app is None:
            return self._unsupported("rate_limit", worker=worker, task_name=task_name, rate_limit=rate_limit)
        self.celery_app.control.rate_limit(task_name, rate_limit, destination=[worker], reply=False)
        return {
            "accepted": True,
            "worker": worker,
            "task_name": task_name,
            "rate_limit": rate_limit,
            "action": "rate_limit",
        }

    def publish_control_command(
        self, *, node_id: str, role: str, action: str, concurrency: int = 1, queue: str = ""
    ) -> dict:
        if self.celery_app is None:
            return self._unsupported(
                "publish_control_command",
                node_id=node_id,
                role=role,
                action_type=action,
                concurrency=concurrency,
            )

        normalized_node = _normalize_node(node_id)
        routing_queue = queue.strip() or _control_queue(self.control_queue_prefix, normalized_node)
        command = {
            "action": action,
            "role": role,
            "node_id": normalized_node,
            "concurrency": max(1, int(concurrency)),
            "requested_at": utc_now_iso(),
        }
        self.celery_app.send_task(
            self.control_task_name,
            args=[command],
            queue=routing_queue,
            routing_key=routing_queue,
            serializer="json",
        )
        return {
            "accepted": True,
            "action": action,
            "role": role,
            "node_id": normalized_node,
            "queue": routing_queue,
            "command": command,
        }


def _extract_concurrency(payload: dict | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    pool = payload.get("pool")
    if isinstance(pool, dict):
        value = pool.get("max-concurrency")
        if isinstance(value, int):
            return max(1, value)
        if isinstance(value, str) and value.isdigit():
            return max(1, int(value))
        procs = pool.get("processes")
        if isinstance(procs, list) and procs:
            return len(procs)
    value = payload.get("concurrency")
    if isinstance(value, int):
        return max(1, value)
    if isinstance(value, str) and value.isdigit():
        return max(1, int(value))
    return None


def _extract_pool_impl(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    pool = payload.get("pool")
    if not isinstance(pool, dict):
        return ""
    value = pool.get("implementation")
    if isinstance(value, str):
        return value
    return ""


def _normalize_node(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).upper()


def _control_queue(prefix: str, node_id: str) -> str:
    if node_id:
        return f"{prefix}@{node_id}"
    return prefix


def _build_celery_app(redis_url: str):
    try:
        from celery import Celery
    except Exception:
        return None

    app = Celery("media-shuttle-api-control", broker=redis_url, backend=redis_url)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
    )
    return app
