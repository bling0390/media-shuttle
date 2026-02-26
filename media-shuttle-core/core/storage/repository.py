from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from threading import Lock
from typing import Iterable

from ..enums import TaskStatus
from ..models import TaskPayload, TaskRecord, utc_now


class TaskRepository:
    def create(self, record: TaskRecord) -> None:
        raise NotImplementedError

    def get(self, task_id: str) -> TaskRecord | None:
        raise NotImplementedError

    def list(self, status: TaskStatus | None = None, limit: int = 20) -> list[TaskRecord]:
        raise NotImplementedError

    def update_status(self, task_id: str, status: TaskStatus, message: str = "") -> TaskRecord | None:
        raise NotImplementedError

    def queue_stats(self) -> dict[str, int]:
        raise NotImplementedError


class InMemoryTaskRepository(TaskRepository):
    def __init__(self) -> None:
        self._items: dict[str, TaskRecord] = {}
        self._lock = Lock()

    def create(self, record: TaskRecord) -> None:
        with self._lock:
            self._items[record.task_id] = record

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            record = self._items.get(task_id)
            return replace(record) if record else None

    def list(self, status: TaskStatus | None = None, limit: int = 20) -> list[TaskRecord]:
        with self._lock:
            values: Iterable[TaskRecord] = self._items.values()
            if status is not None:
                values = [item for item in values if item.status == status]
            items = sorted(values, key=lambda item: item.created_at, reverse=True)
            return [replace(item) for item in items[:limit]]

    def update_status(self, task_id: str, status: TaskStatus, message: str = "") -> TaskRecord | None:
        with self._lock:
            record = self._items.get(task_id)
            if not record:
                return None
            record.status = status
            record.message = message
            record.updated_at = utc_now()
            return replace(record)

    def queue_stats(self) -> dict[str, int]:
        with self._lock:
            parse = len([x for x in self._items.values() if x.status == TaskStatus.PARSING])
            download = len([x for x in self._items.values() if x.status == TaskStatus.DOWNLOADING])
            upload = len([x for x in self._items.values() if x.status == TaskStatus.UPLOADING])
        return {"parse": parse, "download": download, "upload": upload}


def _parse_datetime(raw: datetime | str) -> datetime:
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _isoformat(value: datetime | str) -> str:
    if isinstance(value, str):
        return value
    return value.isoformat().replace("+00:00", "Z")


class MongoTaskRepository(TaskRepository):
    def __init__(
        self,
        mongo_uri: str = "mongodb://localhost:27017",
        db_name: str = "media_shuttle",
        collection_name: str = "tasks",
        client=None,
    ) -> None:
        if client is None:
            try:
                from pymongo import MongoClient
            except Exception as exc:
                raise RuntimeError("pymongo is required for MongoTaskRepository") from exc
            client = MongoClient(mongo_uri)
        self._collection = client[db_name][collection_name]

    @staticmethod
    def _to_document(record: TaskRecord) -> dict:
        return {
            "_id": record.task_id,
            "task_id": record.task_id,
            "idempotency_key": record.idempotency_key,
            "status": record.status.value,
            "message": record.message,
            "requester_id": record.payload.requester_id,
            "url": record.payload.url,
            "target": record.payload.target,
            "destination": record.payload.destination,
            "created_at": _isoformat(record.created_at),
            "updated_at": _isoformat(record.updated_at),
        }

    @staticmethod
    def _from_document(doc: dict | None) -> TaskRecord | None:
        if doc is None:
            return None
        return TaskRecord(
            task_id=doc["task_id"],
            idempotency_key=doc["idempotency_key"],
            payload=TaskPayload(
                url=doc["url"],
                requester_id=doc["requester_id"],
                target=doc["target"],
                destination=doc["destination"],
            ),
            status=TaskStatus(doc["status"]),
            message=doc.get("message", ""),
            created_at=_parse_datetime(doc["created_at"]),
            updated_at=_parse_datetime(doc["updated_at"]),
        )

    def create(self, record: TaskRecord) -> None:
        doc = self._to_document(record)
        self._collection.replace_one({"_id": record.task_id}, doc, upsert=True)

    def get(self, task_id: str) -> TaskRecord | None:
        doc = self._collection.find_one({"_id": task_id})
        return self._from_document(doc)

    def list(self, status: TaskStatus | None = None, limit: int = 20) -> list[TaskRecord]:
        query = {"status": status.value} if status else {}
        docs = list(self._collection.find(query, limit=limit, sort=[("created_at", -1)]))
        return [item for item in [self._from_document(deepcopy(doc)) for doc in docs] if item is not None]

    def update_status(self, task_id: str, status: TaskStatus, message: str = "") -> TaskRecord | None:
        self._collection.update_one(
            {"_id": task_id},
            {
                "$set": {
                    "status": status.value,
                    "message": message,
                    "updated_at": _isoformat(utc_now()),
                }
            },
        )
        return self.get(task_id)

    def queue_stats(self) -> dict[str, int]:
        return {
            "parse": self._collection.count_documents({"status": TaskStatus.PARSING.value}),
            "download": self._collection.count_documents({"status": TaskStatus.DOWNLOADING.value}),
            "upload": self._collection.count_documents({"status": TaskStatus.UPLOADING.value}),
        }
