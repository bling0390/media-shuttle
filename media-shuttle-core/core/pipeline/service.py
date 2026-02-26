from __future__ import annotations

from dataclasses import dataclass

from ..enums import TaskStatus
from ..models import TaskRecord
from ..plugins.parsers import ParserRegistry, default_registry as default_parser_registry
from ..plugins.downloaders import DownloaderRegistry, default_registry as default_downloader_registry
from ..plugins.uploaders import UploaderRegistry, default_registry as default_uploader_registry
from ..storage.repository import TaskRepository


@dataclass
class PipelineService:
    repository: TaskRepository
    parser_registry: ParserRegistry
    downloader_registry: DownloaderRegistry
    uploader_registry: UploaderRegistry

    def run(self, task_id: str) -> TaskRecord:
        task = self.repository.get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")

        self.repository.update_status(task_id, TaskStatus.PARSING)
        parsed_sources = self.parser_registry.parse(task.payload.url)
        if not parsed_sources:
            raise ValueError("no parsed source found")

        self.repository.update_status(task_id, TaskStatus.DOWNLOADING)
        uploads: list[str] = []
        for source in parsed_sources:
            download = self.downloader_registry.download(source)

            self.repository.update_status(task_id, TaskStatus.UPLOADING)
            upload = self.uploader_registry.upload(task.payload.target, download, task.payload.destination)
            uploads.append(upload.location)

        message = uploads[0] if len(uploads) == 1 else "\n".join(uploads)
        done = self.repository.update_status(task_id, TaskStatus.SUCCEEDED, message=message)
        if done is None:
            raise KeyError(f"task not found after update: {task_id}")
        return done


def build_pipeline_service(repository: TaskRepository) -> PipelineService:
    return PipelineService(
        repository=repository,
        parser_registry=default_parser_registry(),
        downloader_registry=default_downloader_registry(),
        uploader_registry=default_uploader_registry(),
    )
