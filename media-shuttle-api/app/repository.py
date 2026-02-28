from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from threading import Lock

from .models import TaskRecord, utc_now_iso


class TaskRepository:
    def create(self, record: TaskRecord) -> None:
        raise NotImplementedError

    def get(self, task_id: str) -> TaskRecord | None:
        raise NotImplementedError

    def list(self, status: str | None, limit: int) -> list[TaskRecord]:
        raise NotImplementedError

    def stats(self) -> dict[str, int]:
        raise NotImplementedError

    def update_status(self, task_id: str, status: str, message: str = "") -> TaskRecord | None:
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
            item = self._items.get(task_id)
            return replace(item) if item else None

    def list(self, status: str | None, limit: int) -> list[TaskRecord]:
        with self._lock:
            values = list(self._items.values())
            if status:
                values = [item for item in values if item.status == status]
            values = sorted(values, key=lambda item: item.created_at, reverse=True)
            return [replace(item) for item in values[:limit]]

    def stats(self) -> dict[str, int]:
        with self._lock:
            parse = len([x for x in self._items.values() if x.status == "PARSING"])
            download = len([x for x in self._items.values() if x.status == "DOWNLOADING"])
            upload = len([x for x in self._items.values() if x.status == "UPLOADING"])
        return {"parse": parse, "download": download, "upload": upload}

    def update_status(self, task_id: str, status: str, message: str = "") -> TaskRecord | None:
        with self._lock:
            item = self._items.get(task_id)
            if not item:
                return None
            item.status = status
            item.message = message
            item.updated_at = utc_now_iso()
            return replace(item)


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
            "status": record.status,
            "requester_id": record.requester_id,
            "url": record.url,
            "target": record.target,
            "destination": record.destination,
            "message": record.message,
            "sources": list(record.sources),
            "artifacts": list(record.artifacts),
            "last_error": record.last_error,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @staticmethod
    def _from_document(doc: dict | None) -> TaskRecord | None:
        if doc is None:
            return None
        return TaskRecord(
            task_id=doc["task_id"],
            idempotency_key=doc["idempotency_key"],
            status=doc["status"],
            requester_id=doc["requester_id"],
            url=doc["url"],
            target=doc["target"],
            destination=doc["destination"],
            message=doc.get("message", ""),
            sources=[item for item in doc.get("sources", []) if isinstance(item, dict)],
            artifacts=[item for item in doc.get("artifacts", []) if isinstance(item, dict)],
            last_error=doc.get("last_error", ""),
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )

    def create(self, record: TaskRecord) -> None:
        doc = self._to_document(record)
        # Replace to support idempotent create in retry/replay cases.
        self._collection.replace_one({"_id": record.task_id}, doc, upsert=True)

    def get(self, task_id: str) -> TaskRecord | None:
        doc = self._collection.find_one({"_id": task_id})
        return self._from_document(doc)

    def list(self, status: str | None, limit: int) -> list[TaskRecord]:
        query = {"status": status} if status else {}
        docs = list(self._collection.find(query, limit=limit, sort=[("created_at", -1)]))
        return [item for item in [self._from_document(deepcopy(doc)) for doc in docs] if item is not None]

    def stats(self) -> dict[str, int]:
        return {
            "parse": self._collection.count_documents({"status": "PARSING"}),
            "download": self._collection.count_documents({"status": "DOWNLOADING"}),
            "upload": self._collection.count_documents({"status": "UPLOADING"}),
        }

    def update_status(self, task_id: str, status: str, message: str = "") -> TaskRecord | None:
        updated_at = utc_now_iso()
        self._collection.update_one(
            {"_id": task_id},
            {"$set": {"status": status, "message": message, "updated_at": updated_at}},
        )
        return self.get(task_id)
