from __future__ import annotations

import uuid
from dataclasses import dataclass

from .enums import TaskStatus
from .models import TaskPayload, TaskRecord
from .pipeline.service import PipelineService
from .queue.contracts import validate_task_created_event
from .storage.repository import TaskRepository
from .utils import make_idempotency_key


@dataclass
class CoreService:
    repository: TaskRepository
    pipeline: PipelineService

    def create_task_from_event(self, event: dict) -> TaskRecord:
        validate_task_created_event(event)
        task_id = event.get("task_id") or str(uuid.uuid4())

        existing = self.repository.get(task_id)
        if existing is not None:
            return existing

        payload = TaskPayload(
            url=event["payload"]["url"],
            requester_id=event["payload"]["requester_id"],
            target=event["payload"]["target"],
            destination=event["payload"]["destination"],
        )

        record = TaskRecord(
            task_id=task_id,
            idempotency_key=event.get("idempotency_key") or make_idempotency_key(payload.url, payload.requester_id),
            payload=payload,
            status=TaskStatus.QUEUED,
        )
        self.repository.create(record)
        return record

    def run_task(self, task_id: str) -> TaskRecord:
        try:
            return self.pipeline.run(task_id)
        except Exception as exc:
            task = self.repository.update_status(task_id, TaskStatus.FAILED, str(exc))
            if task is None:
                raise
            return task
