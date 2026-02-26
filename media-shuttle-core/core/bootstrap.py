from __future__ import annotations

import os

from .pipeline.service import build_pipeline_service
from .service import CoreService
from .storage.repository import InMemoryTaskRepository, MongoTaskRepository


def build_core_service(repository_backend: str | None = None, mongo_client=None) -> CoreService:
    repository_backend = (repository_backend or os.getenv("MEDIA_SHUTTLE_STORAGE_BACKEND", "memory")).lower()

    if repository_backend == "mongo":
        repository = MongoTaskRepository(
            mongo_uri=os.getenv("MEDIA_SHUTTLE_MONGO_URI", "mongodb://localhost:27017"),
            db_name=os.getenv("MEDIA_SHUTTLE_MONGO_DB", "media_shuttle"),
            collection_name=os.getenv("MEDIA_SHUTTLE_MONGO_TASK_COLLECTION", "tasks"),
            client=mongo_client,
        )
    else:
        repository = InMemoryTaskRepository()

    pipeline = build_pipeline_service(repository)
    return CoreService(repository=repository, pipeline=pipeline)
