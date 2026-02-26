from __future__ import annotations

import json


class TaskCreatedConsumer:
    def pop_created_event(self, timeout_seconds: int = 1) -> dict | None:
        raise NotImplementedError


class InMemoryTaskCreatedConsumer(TaskCreatedConsumer):
    def __init__(self, items: list[dict] | None = None) -> None:
        self._items = items or []

    def pop_created_event(self, timeout_seconds: int = 1) -> dict | None:
        if not self._items:
            return None
        return self._items.pop(0)


class RedisTaskCreatedConsumer(TaskCreatedConsumer):
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        queue_key: str = "media_shuttle:task_created",
        client=None,
    ) -> None:
        if client is None:
            try:
                import redis
            except Exception as exc:
                raise RuntimeError("redis is required for RedisTaskCreatedConsumer") from exc
            client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._client = client
        self._queue_key = queue_key

    def pop_created_event(self, timeout_seconds: int = 1) -> dict | None:
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
