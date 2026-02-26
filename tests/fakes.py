from __future__ import annotations

from copy import deepcopy
from threading import Lock


class FakeMongoCollection:
    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    def replace_one(self, query: dict, document: dict, upsert: bool = False):
        key = query.get("_id")
        if key is None:
            return
        if upsert or key in self._items:
            self._items[key] = deepcopy(document)

    def insert_one(self, document: dict):
        self._items[document["_id"]] = deepcopy(document)

    def find_one(self, query: dict):
        key = query.get("_id")
        if key is not None:
            item = self._items.get(key)
            return deepcopy(item) if item else None
        for item in self._items.values():
            matched = True
            for k, v in query.items():
                if item.get(k) != v:
                    matched = False
                    break
            if matched:
                return deepcopy(item)
        return None

    def find(self, query: dict | None = None, limit: int = 0, sort=None):
        query = query or {}
        items = []
        for item in self._items.values():
            matched = True
            for k, v in query.items():
                if item.get(k) != v:
                    matched = False
                    break
            if matched:
                items.append(deepcopy(item))

        if sort:
            key, direction = sort[0]
            reverse = direction < 0
            items = sorted(items, key=lambda x: x.get(key), reverse=reverse)

        if limit:
            items = items[:limit]
        return items

    def update_one(self, query: dict, update: dict):
        key = query.get("_id")
        item = self._items.get(key)
        if not item:
            return
        set_fields = update.get("$set", {})
        item.update(deepcopy(set_fields))

    def count_documents(self, query: dict):
        return len(self.find(query))


class FakeMongoDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeMongoCollection] = {}

    def __getitem__(self, collection_name: str):
        if collection_name not in self._collections:
            self._collections[collection_name] = FakeMongoCollection()
        return self._collections[collection_name]


class FakeMongoClient:
    def __init__(self) -> None:
        self._dbs: dict[str, FakeMongoDatabase] = {}

    def __getitem__(self, db_name: str):
        if db_name not in self._dbs:
            self._dbs[db_name] = FakeMongoDatabase()
        return self._dbs[db_name]


class FakeRedis:
    def __init__(self) -> None:
        self._queues: dict[str, list[str]] = {}
        self._lock = Lock()

    def rpush(self, key: str, value: str):
        with self._lock:
            self._queues.setdefault(key, []).append(value)

    def blpop(self, key: str, timeout: int = 0):
        keys = key if isinstance(key, list) else [key]
        with self._lock:
            for name in keys:
                queue = self._queues.setdefault(name, [])
                if queue:
                    value = queue.pop(0)
                    return name, value
        return None

    def llen(self, key: str) -> int:
        with self._lock:
            return len(self._queues.setdefault(key, []))
