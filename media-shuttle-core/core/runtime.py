from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from .bootstrap import build_core_service
from .queue.consumer import InMemoryTaskCreatedConsumer, RedisTaskCreatedConsumer
from .queue.publisher import InMemoryEventPublisher, RedisEventPublisher
from .worker import CoreWorker


@dataclass
class RuntimeConfig:
    queue_backend: str = "memory"
    created_queue_key: str = "media_shuttle:task_created"
    retry_queue_key: str = "media_shuttle:task_retry"
    max_retries: int = 2
    concurrency: int = 1
    poll_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            queue_backend=os.getenv("MEDIA_SHUTTLE_QUEUE_BACKEND", "memory").lower(),
            created_queue_key=os.getenv("MEDIA_SHUTTLE_CREATED_QUEUE_KEY", "media_shuttle:task_created"),
            retry_queue_key=os.getenv("MEDIA_SHUTTLE_RETRY_QUEUE_KEY", "media_shuttle:task_retry"),
            max_retries=int(os.getenv("MEDIA_SHUTTLE_MAX_RETRIES", "2")),
            concurrency=max(1, int(os.getenv("MEDIA_SHUTTLE_CORE_CONCURRENCY", "1"))),
            poll_seconds=float(os.getenv("MEDIA_SHUTTLE_CORE_POLL_SECONDS", "1.0")),
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CoreRuntime:
    def __init__(
        self,
        service=None,
        config: RuntimeConfig | None = None,
        redis_client=None,
        in_memory_events: list[dict] | None = None,
    ) -> None:
        self.config = config or RuntimeConfig.from_env()
        self.worker = CoreWorker(service=service or build_core_service())

        if self.config.queue_backend == "redis":
            self.created_consumer = RedisTaskCreatedConsumer(
                redis_url=os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0"),
                queue_key=self.config.created_queue_key,
                client=redis_client,
            )
            self.retry_consumer = RedisTaskCreatedConsumer(
                redis_url=os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0"),
                queue_key=self.config.retry_queue_key,
                client=redis_client,
            )
            self.publisher = RedisEventPublisher(
                redis_url=os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0"),
                client=redis_client,
            )
        else:
            events = in_memory_events or []
            self.created_consumer = InMemoryTaskCreatedConsumer(events)
            self.retry_consumer = InMemoryTaskCreatedConsumer([])
            self.publisher = InMemoryEventPublisher(
                {
                    self.config.retry_queue_key: self.retry_consumer._items,
                }
            )

    def _pop_next_event(self, timeout_seconds: int) -> dict | None:
        # Priority consume from retry queue to reduce failure tail latency.
        event = self.retry_consumer.pop_created_event(timeout_seconds=0)
        if event is not None:
            return event
        return self.created_consumer.pop_created_event(timeout_seconds=timeout_seconds)

    def _route_failure(self, event: dict, reason: str, task_id: str | None = None) -> dict:
        attempt = int(event.get("attempt", 0))
        next_attempt = attempt + 1

        base = dict(event)
        base["attempt"] = next_attempt
        base["last_error"] = reason
        base["updated_at"] = _utc_now_iso()
        if task_id:
            base["task_id"] = task_id

        if attempt < self.config.max_retries:
            self.publisher.publish(self.config.retry_queue_key, base)
            return {
                "state": "retried",
                "task_id": base.get("task_id"),
                "attempt": next_attempt,
                "reason": reason,
            }
        return {
            "state": "failed",
            "task_id": base.get("task_id"),
            "attempt": next_attempt,
            "reason": reason,
        }

    def process_one(self, timeout_seconds: int = 1) -> dict | None:
        event = self._pop_next_event(timeout_seconds=timeout_seconds)
        if event is None:
            return None

        try:
            result = self.worker.handle_event(event)
        except Exception as exc:
            return self._route_failure(event, str(exc), task_id=event.get("task_id"))

        if result.get("status") == "FAILED":
            return self._route_failure(event, result.get("message", "failed"), task_id=result.get("task_id"))

        return {
            "state": "succeeded",
            "task_id": result.get("task_id"),
            "attempt": int(event.get("attempt", 0)),
            "result": result,
        }

    def run_workers_once(self, steps_per_worker: int = 1, timeout_seconds: int = 0) -> int:
        def _consume_n() -> int:
            count = 0
            for _ in range(max(1, steps_per_worker)):
                out = self.process_one(timeout_seconds=timeout_seconds)
                if out is None:
                    break
                count += 1
            return count

        with ThreadPoolExecutor(max_workers=max(1, self.config.concurrency)) as pool:
            futures = [pool.submit(_consume_n) for _ in range(max(1, self.config.concurrency))]
        return sum(item.result() for item in futures)

    def run_forever(self) -> None:
        stop_event = threading.Event()

        def _loop() -> None:
            while not stop_event.is_set():
                out = self.process_one(timeout_seconds=max(1, int(self.config.poll_seconds)))
                if out is None:
                    time.sleep(self.config.poll_seconds)

        threads: list[threading.Thread] = []
        for idx in range(max(1, self.config.concurrency)):
            t = threading.Thread(target=_loop, name=f"core-worker-{idx}", daemon=True)
            threads.append(t)
            t.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_event.set()
            for thread in threads:
                thread.join(timeout=1)


def run_forever(poll_seconds: float = 1.0) -> None:
    config = RuntimeConfig.from_env()
    config.poll_seconds = poll_seconds
    runtime = CoreRuntime(config=config)
    runtime.run_forever()
