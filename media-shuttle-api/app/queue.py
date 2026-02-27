from __future__ import annotations

import json
import os
from threading import Lock


class TaskPublisher:
    def publish_created_event(self, event: dict) -> None:
        raise NotImplementedError

    def pop_created_event(self, timeout_seconds: int = 1) -> dict | None:
        raise NotImplementedError


class InMemoryTaskPublisher(TaskPublisher):
    def __init__(self) -> None:
        self._items: list[dict] = []
        self._lock = Lock()

    @property
    def items(self) -> list[dict]:
        with self._lock:
            return list(self._items)

    def publish_created_event(self, event: dict) -> None:
        with self._lock:
            self._items.append(event)

    def pop_created_event(self, timeout_seconds: int = 1) -> dict | None:
        with self._lock:
            if not self._items:
                return None
            return self._items.pop(0)


class RedisTaskPublisher(TaskPublisher):
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        queue_key: str = "media_shuttle:task_created",
        client=None,
        use_celery: bool | None = None,
        celery_app=None,
        celery_task_name: str | None = None,
    ) -> None:
        if use_celery is None:
            raw = os.getenv("MEDIA_SHUTTLE_REDIS_PUBLISH_MODE", "celery").strip().lower()
            use_celery = raw == "celery"

        self._queue_key = queue_key
        self._task_name = celery_task_name or os.getenv(
            "MEDIA_SHUTTLE_CORE_CREATED_TASK_NAME", "core.queue.tasks.process_created_event"
        )
        self._celery_app = None
        self._client = None

        # Tests can pass fake redis clients and keep legacy list behavior.
        if use_celery and client is None:
            self._celery_app = celery_app or _build_celery_app(redis_url)

        if self._celery_app is None:
            if client is None:
                try:
                    import redis
                except Exception as exc:
                    raise RuntimeError("redis is required for RedisTaskPublisher") from exc
                client = redis.Redis.from_url(redis_url, decode_responses=True)
            self._client = client

    def publish_created_event(self, event: dict) -> None:
        if self._celery_app is not None:
            self._celery_app.send_task(
                self._task_name,
                args=[event],
                queue=self._queue_key,
                routing_key=self._queue_key,
                serializer="json",
            )
            return
        self._client.rpush(self._queue_key, json.dumps(event))

    def pop_created_event(self, timeout_seconds: int = 1) -> dict | None:
        if self._client is None:
            raise RuntimeError("pop_created_event is not supported when redis publish mode is celery")
        item = self._client.blpop(self._queue_key, timeout=timeout_seconds)
        if not item:
            return None
        if isinstance(item, (list, tuple)) and len(item) == 2:
            payload = item[1]
        else:
            payload = item
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return json.loads(payload)


def _build_celery_app(redis_url: str):
    try:
        from celery import Celery
    except Exception:
        return None

    app = Celery("media-shuttle-api", broker=redis_url, backend=redis_url)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
    )
    return app
