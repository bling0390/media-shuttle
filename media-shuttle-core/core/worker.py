from __future__ import annotations

from .bootstrap import build_core_service
from .queue.consumer import TaskCreatedConsumer


def handle_created_event(event: dict) -> dict:
    service = build_core_service()
    record = service.create_task_from_event(event)
    result = service.run_task(record.task_id)
    return result.to_dict()


class CoreWorker:
    def __init__(self, service=None) -> None:
        self.service = service or build_core_service()

    def handle_event(self, event: dict) -> dict:
        record = self.service.create_task_from_event(event)
        result = self.service.run_task(record.task_id)
        return result.to_dict()

    def consume_once(self, consumer: TaskCreatedConsumer, timeout_seconds: int = 1) -> dict | None:
        event = consumer.pop_created_event(timeout_seconds=timeout_seconds)
        if event is None:
            return None
        return self.handle_event(event)
