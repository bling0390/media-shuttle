from __future__ import annotations

import json


class EventPublisher:
    def publish(self, queue_key: str, event: dict) -> None:
        raise NotImplementedError


class InMemoryEventPublisher(EventPublisher):
    def __init__(self, buckets: dict[str, list[dict]] | None = None) -> None:
        self.buckets = buckets or {}

    def publish(self, queue_key: str, event: dict) -> None:
        self.buckets.setdefault(queue_key, []).append(dict(event))


class RedisEventPublisher(EventPublisher):
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        client=None,
    ) -> None:
        if client is None:
            try:
                import redis
            except Exception as exc:
                raise RuntimeError("redis is required for RedisEventPublisher") from exc
            client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._client = client

    def publish(self, queue_key: str, event: dict) -> None:
        self._client.rpush(queue_key, json.dumps(event))
