from __future__ import annotations

import os
from dataclasses import dataclass

from .queue import InMemoryTaskPublisher, RedisTaskPublisher
from .repository import (
    InMemoryTaskRepository,
    InMemoryWorkerRepository,
    MongoTaskRepository,
    MongoWorkerRepository,
)
from .service import ApiService
from .worker_control import CeleryWorkerControl, InMemoryWorkerControl


@dataclass
class Container:
    repository: object
    worker_repository: object
    publisher: object
    worker_control: object
    service: ApiService


def build_container(
    repository_backend: str | None = None,
    queue_backend: str | None = None,
    mongo_client=None,
    redis_client=None,
    worker_control=None,
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
        worker_repository = MongoWorkerRepository(
            mongo_uri=os.getenv("MEDIA_SHUTTLE_MONGO_URI", "mongodb://localhost:27017"),
            db_name=os.getenv("MEDIA_SHUTTLE_MONGO_DB", "media_shuttle"),
            collection_name=os.getenv("MEDIA_SHUTTLE_MONGO_WORKER_COLLECTION", "workers"),
            client=mongo_client,
        )
    else:
        repository = InMemoryTaskRepository()
        worker_repository = InMemoryWorkerRepository()

    if queue_backend == "redis":
        publisher = RedisTaskPublisher(
            redis_url=os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0"),
            queue_key=os.getenv("MEDIA_SHUTTLE_CREATED_QUEUE_KEY", "media_shuttle:task_created"),
            client=redis_client,
        )
    else:
        publisher = InMemoryTaskPublisher()

    if worker_control is None:
        if queue_backend == "redis":
            worker_control = CeleryWorkerControl(
                redis_url=os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0"),
                control_queue_prefix=os.getenv("MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY", "media_shuttle:worker_control"),
                control_task_name=os.getenv("MEDIA_SHUTTLE_CORE_WORKER_CONTROL_TASK_NAME", "core.queue.tasks.apply_worker_control"),
            )
        else:
            worker_control = InMemoryWorkerControl()

    service = ApiService(
        repository=repository,
        publisher=publisher,
        worker_repository=worker_repository,
        worker_control=worker_control,
    )
    return Container(
        repository=repository,
        worker_repository=worker_repository,
        publisher=publisher,
        worker_control=worker_control,
        service=service,
    )
