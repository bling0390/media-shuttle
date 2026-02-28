from __future__ import annotations

import uuid
from dataclasses import dataclass

from .contracts import validate_create_request
from .models import CreateTaskRequest, TaskRecord, utc_now_iso
from .queue import TaskPublisher
from .repository import TaskRepository
from .utils import make_idempotency_key


@dataclass
class ApiService:
    repository: TaskRepository
    publisher: TaskPublisher

    def create_parse_task(self, request: CreateTaskRequest) -> TaskRecord:
        payload = {
            "url": request.url,
            "requester_id": request.requester_id,
            "target": request.target,
            "destination": request.destination,
        }
        validate_create_request(payload)

        task_id = str(uuid.uuid4())
        idempotency_key = make_idempotency_key(request.url, request.requester_id)
        timestamp = utc_now_iso()

        event = {
            "spec_version": "task.created.v1",
            "task_id": task_id,
            "task_type": "parse_link",
            "idempotency_key": idempotency_key,
            "created_at": timestamp,
            "payload": payload,
        }

        record = TaskRecord(
            task_id=task_id,
            idempotency_key=idempotency_key,
            status="QUEUED",
            requester_id=request.requester_id,
            url=request.url,
            target=request.target,
            destination=request.destination,
            created_at=timestamp,
            updated_at=timestamp,
        )

        self.repository.create(record)
        self.publisher.publish_created_event(event)
        return record

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.repository.get(task_id)

    def list_tasks(self, status: str | None, limit: int) -> list[TaskRecord]:
        return self.repository.list(status=status, limit=limit)

    def queue_stats(self) -> dict[str, int]:
        return self.repository.stats()

    # phase 8: operation endpoints backend hooks
    def admin_worker_action(self, worker: str, queue: str, concurrency: int) -> dict:
        return {"worker": worker, "queue": queue, "concurrency": concurrency, "accepted": True}

    def admin_rate_limit_action(self, worker: str, task_type: str, rate_limit: str) -> dict:
        return {"worker": worker, "task_type": task_type, "rate_limit": rate_limit, "accepted": True}

    def _build_created_event(self, task: TaskRecord) -> dict:
        return {
            "spec_version": "task.created.v1",
            "task_id": task.task_id,
            "task_type": "parse_link",
            "idempotency_key": task.idempotency_key,
            "created_at": utc_now_iso(),
            "payload": {
                "url": task.url,
                "requester_id": task.requester_id,
                "target": task.target,
                "destination": task.destination,
            },
        }

    def admin_retry_action(self, mode: str, task_id: str | None = None, limit: int = 20) -> dict:
        mode_key = (mode or "failed").strip().lower()
        max_limit = max(1, min(int(limit), 200))

        retried: list[str] = []
        skipped = 0

        if task_id:
            task = self.repository.get(task_id)
            if task is None:
                return {
                    "mode": mode_key,
                    "task_id": task_id,
                    "accepted": False,
                    "reason": "task_not_found",
                    "retried": 0,
                    "skipped": 1,
                }
            if task.status != "FAILED":
                return {
                    "mode": mode_key,
                    "task_id": task_id,
                    "accepted": False,
                    "reason": "task_not_failed",
                    "retried": 0,
                    "skipped": 1,
                }
            self.repository.update_status(task.task_id, "QUEUED", "")
            self.publisher.publish_created_event(self._build_created_event(task))
            return {
                "mode": mode_key,
                "task_id": task_id,
                "accepted": True,
                "retried": 1,
                "skipped": 0,
                "task_ids": [task.task_id],
            }

        if mode_key not in {"failed", "both"}:
            return {
                "mode": mode_key,
                "accepted": False,
                "reason": "unsupported_mode",
                "retried": 0,
                "skipped": 0,
            }

        failed_items = self.repository.list(status="FAILED", limit=max_limit)
        for item in failed_items:
            if item.status != "FAILED":
                skipped += 1
                continue
            self.repository.update_status(item.task_id, "QUEUED", "")
            self.publisher.publish_created_event(self._build_created_event(item))
            retried.append(item.task_id)

        return {
            "mode": mode_key,
            "accepted": True,
            "retried": len(retried),
            "skipped": skipped,
            "task_ids": retried,
        }

    def admin_setting_action(self, key: str, value: str) -> dict:
        return {"key": key, "value": value, "accepted": True}
