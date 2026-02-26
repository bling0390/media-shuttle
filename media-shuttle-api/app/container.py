from __future__ import annotations

import os
from dataclasses import dataclass

from .queue import InMemoryTaskPublisher, RedisTaskPublisher
from .repository import InMemoryTaskRepository, MongoTaskRepository
from .service import ApiService


@dataclass
class Container:
    repository: object
    publisher: object
    service: ApiService


def build_container(
    repository_backend: str | None = None,
    queue_backend: str | None = None,
    mongo_client=None,
    redis_client=None,
) -> Container:
    repository_backend = (repository_backend or os.getenv("MEDIA_SHUTTLE_STORAGE_BACKEND", "memory")).lower()
    queue_backend = (queue_backend or os.getenv("MEDIA_SHUTTLE_QUEUE_BACKEND", "memory")).lower()

    if repository_backend == "mongo":
        repository = MongoTaskRepository(
            mongo_uri=os.getenv("MEDIA_SHUTTLE_MONGO_URI", "mongodb://localhost:27017"),
            db_name=os.getenv("MEDIA_SHUTTLE_MONGO_DB", "media_shuttle"),
            collection_name=os.getenv("MEDIA_SHUTTLE_MONGO_TASK_COLLECTION", "tasks"),
            client=mongo_client,
        )
    else:
        repository = InMemoryTaskRepository()

    if queue_backend == "redis":
        publisher = RedisTaskPublisher(
            redis_url=os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0"),
            queue_key=os.getenv("MEDIA_SHUTTLE_CREATED_QUEUE_KEY", "media_shuttle:task_created"),
            client=redis_client,
        )
    else:
        publisher = InMemoryTaskPublisher()

    service = ApiService(repository=repository, publisher=publisher)
    return Container(repository=repository, publisher=publisher, service=service)
