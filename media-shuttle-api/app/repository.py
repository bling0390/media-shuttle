from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from dataclasses import replace
from threading import Lock
from typing import Any

from .models import TaskRecord, WorkerRecord, utc_now_iso


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


class WorkerRepository:
    def upsert(self, record: WorkerRecord) -> WorkerRecord:
        raise NotImplementedError

    def get(self, hostname: str) -> WorkerRecord | None:
        raise NotImplementedError

    def list(self, status: str | None = None, limit: int = 100) -> list[WorkerRecord]:
        raise NotImplementedError

    def patch(self, hostname: str, **fields) -> WorkerRecord | None:
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


class InMemoryWorkerRepository(WorkerRepository):
    def __init__(self) -> None:
        self._items: dict[str, WorkerRecord] = {}
        self._lock = Lock()

    def upsert(self, record: WorkerRecord) -> WorkerRecord:
        with self._lock:
            self._items[record.hostname] = replace(record)
            return replace(record)

    def get(self, hostname: str) -> WorkerRecord | None:
        with self._lock:
            item = self._items.get(hostname)
            return replace(item) if item else None

    def list(self, status: str | None = None, limit: int = 100) -> list[WorkerRecord]:
        with self._lock:
            values = list(self._items.values())
            if status:
                values = [item for item in values if item.status == status]
            values = sorted(values, key=lambda item: item.updated_at, reverse=True)
            return [replace(item) for item in values[:limit]]

    def patch(self, hostname: str, **fields) -> WorkerRecord | None:
        with self._lock:
            current = self._items.get(hostname)
            if current is None:
                return None
            payload = asdict(current)
            for key, value in fields.items():
                if value is not None:
                    payload[key] = value
            updated = WorkerRecord(**payload)
            self._items[hostname] = replace(updated)
            return replace(updated)


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


class MongoWorkerRepository(WorkerRepository):
    def __init__(
        self,
        mongo_uri: str = "mongodb://localhost:27017",
        db_name: str = "media_shuttle",
        collection_name: str = "workers",
        client=None,
    ) -> None:
        if client is None:
            try:
                from pymongo import MongoClient
            except Exception as exc:
                raise RuntimeError("pymongo is required for MongoWorkerRepository") from exc
            client = MongoClient(mongo_uri)
        self._collection = client[db_name][collection_name]

    @staticmethod
    def _to_document(record: WorkerRecord) -> dict:
        return {
            "_id": record.hostname,
            "hostname": record.hostname,
            "role": record.role,
            "queue": record.queue,
            "queues": list(record.queues),
            "status": record.status,
            "concurrency": int(record.concurrency),
            "desired_concurrency": int(record.desired_concurrency),
            "node_id": record.node_id,
            "pid": record.pid,
            "exit_code": record.exit_code,
            "rate_limits": dict(record.rate_limits),
            "last_error": record.last_error,
            "started_at": record.started_at,
            "last_heartbeat_at": record.last_heartbeat_at,
            "updated_at": record.updated_at,
        }

    @staticmethod
    def _from_document(doc: dict | None) -> WorkerRecord | None:
        if doc is None:
            return None
        queues = [item for item in doc.get("queues", []) if isinstance(item, str)]
        queue = doc.get("queue", "")
        if not queue and queues:
            queue = ",".join(queues)
        return WorkerRecord(
            hostname=doc["hostname"],
            role=doc.get("role", ""),
            queue=queue,
            queues=queues,
            status=doc.get("status", "UNKNOWN"),
            concurrency=max(1, int(doc.get("concurrency", 1))),
            desired_concurrency=max(1, int(doc.get("desired_concurrency", doc.get("concurrency", 1)))),
            node_id=doc.get("node_id", ""),
            pid=doc.get("pid"),
            exit_code=doc.get("exit_code"),
            rate_limits={k: str(v) for k, v in dict(doc.get("rate_limits") or {}).items()},
            last_error=doc.get("last_error", ""),
            started_at=doc.get("started_at", utc_now_iso()),
            last_heartbeat_at=doc.get("last_heartbeat_at", doc.get("updated_at", utc_now_iso())),
            updated_at=doc.get("updated_at", utc_now_iso()),
        )

    def upsert(self, record: WorkerRecord) -> WorkerRecord:
        doc = self._to_document(record)
        self._collection.replace_one({"_id": record.hostname}, doc, upsert=True)
        got = self._from_document(self._collection.find_one({"_id": record.hostname}))
        return got or record

    def get(self, hostname: str) -> WorkerRecord | None:
        doc = self._collection.find_one({"_id": hostname})
        return self._from_document(doc)

    def list(self, status: str | None = None, limit: int = 100) -> list[WorkerRecord]:
        query = {"status": status} if status else {}
        docs = list(self._collection.find(query, limit=limit, sort=[("updated_at", -1)]))
        return [item for item in [self._from_document(deepcopy(doc)) for doc in docs] if item is not None]

    def patch(self, hostname: str, **fields) -> WorkerRecord | None:
        current = self._collection.find_one({"_id": hostname})
        if current is None:
            return None
        next_doc: dict[str, Any] = dict(current)
        for key, value in fields.items():
            if value is not None:
                next_doc[key] = value
        next_doc.setdefault("hostname", hostname)
        next_doc["_id"] = hostname
        self._collection.replace_one({"_id": hostname}, next_doc, upsert=True)
        return self._from_document(next_doc)
