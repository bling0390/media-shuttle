from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class WorkerRegistry:
    def upsert_worker(
        self,
        *,
        hostname: str,
        role: str,
        queues: list[str],
        concurrency: int,
        status: str,
        node_id: str = "",
        pid: int | None = None,
        exit_code: int | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        raise NotImplementedError

    def heartbeat(self, hostname: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def get(self, hostname: str) -> dict[str, Any] | None:
        raise NotImplementedError


class InMemoryWorkerRegistry(WorkerRegistry):
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def upsert_worker(
        self,
        *,
        hostname: str,
        role: str,
        queues: list[str],
        concurrency: int,
        status: str,
        node_id: str = "",
        pid: int | None = None,
        exit_code: int | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._lock:
            current = deepcopy(self._items.get(hostname) or {})
            started_at = current.get("started_at") or now
            next_doc = {
                "_id": hostname,
                "hostname": hostname,
                "role": role,
                "queues": list(queues),
                "queue": ",".join(queues),
                "status": status,
                "concurrency": max(1, int(concurrency)),
                "desired_concurrency": max(1, int(concurrency)),
                "node_id": node_id,
                "pid": pid,
                "exit_code": exit_code,
                "last_error": reason,
                "started_at": started_at,
                "last_heartbeat_at": now,
                "updated_at": now,
            }
            self._items[hostname] = deepcopy(next_doc)
            return deepcopy(next_doc)

    def heartbeat(self, hostname: str) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self._lock:
            current = self._items.get(hostname)
            if current is None:
                return None
            current["last_heartbeat_at"] = now
            current["updated_at"] = now
            return deepcopy(current)

    def get(self, hostname: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(hostname)
            return deepcopy(item) if item else None


class MongoWorkerRegistry(WorkerRegistry):
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
                raise RuntimeError("pymongo is required for MongoWorkerRegistry") from exc
            client = MongoClient(mongo_uri)
        self._collection = client[db_name][collection_name]

    def upsert_worker(
        self,
        *,
        hostname: str,
        role: str,
        queues: list[str],
        concurrency: int,
        status: str,
        node_id: str = "",
        pid: int | None = None,
        exit_code: int | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        now = utc_now_iso()
        current = self._collection.find_one({"_id": hostname}) or {}
        started_at = current.get("started_at") or now
        desired_concurrency = int(current.get("desired_concurrency") or max(1, int(concurrency)))
        doc = {
            "_id": hostname,
            "hostname": hostname,
            "role": role,
            "queues": list(queues),
            "queue": ",".join(queues),
            "status": status,
            "concurrency": max(1, int(concurrency)),
            "desired_concurrency": max(1, desired_concurrency),
            "node_id": node_id,
            "pid": pid,
            "exit_code": exit_code,
            "last_error": reason,
            "started_at": started_at,
            "last_heartbeat_at": now,
            "updated_at": now,
        }
        self._collection.replace_one({"_id": hostname}, doc, upsert=True)
        return doc

    def heartbeat(self, hostname: str) -> dict[str, Any] | None:
        current = self._collection.find_one({"_id": hostname})
        if current is None:
            return None
        now = utc_now_iso()
        current["last_heartbeat_at"] = now
        current["updated_at"] = now
        self._collection.replace_one({"_id": hostname}, current, upsert=True)
        return current

    def get(self, hostname: str) -> dict[str, Any] | None:
        return self._collection.find_one({"_id": hostname})
