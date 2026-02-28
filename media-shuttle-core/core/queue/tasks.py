from __future__ import annotations

import os
import re
import socket
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from ..bootstrap import build_core_service
from ..enums import SourceSite, TaskStatus
from ..models import DownloadResult, ParsedSource
from .celery_app import build_celery_app

TASK_PARSE_CREATED = "core.queue.tasks.process_created_event"
TASK_DOWNLOAD_SOURCE = "core.queue.tasks.process_download_source"
TASK_UPLOAD_RESULT = "core.queue.tasks.process_upload_result"
TASK_FINALIZE = "core.queue.tasks.process_finalize_task"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _created_queue_key() -> str:
    return os.getenv("MEDIA_SHUTTLE_CREATED_QUEUE_KEY", "media_shuttle:task_created")


def _retry_queue_key() -> str:
    return os.getenv("MEDIA_SHUTTLE_RETRY_QUEUE_KEY", "media_shuttle:task_retry")


def _download_queue_prefix() -> str:
    return os.getenv("MEDIA_SHUTTLE_DOWNLOAD_QUEUE_KEY", "media_shuttle:task_download")


def _download_queue_for_site(site: str) -> str:
    suffix = (site or SourceSite.GENERIC.value).upper()
    return f"{_download_queue_prefix()}@{suffix}"


def _normalize_owner_node(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).upper()


def _resolve_owner_node() -> str:
    explicit = os.getenv("MEDIA_SHUTTLE_NODE_ID", "").strip()
    if explicit:
        return _normalize_owner_node(explicit)
    return _normalize_owner_node(socket.gethostname())


def _max_retries() -> int:
    return int(os.getenv("MEDIA_SHUTTLE_MAX_RETRIES", "2"))


def _source_snapshot(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "site": source.get("site", ""),
        "page_url": source.get("page_url", ""),
        "download_url": source.get("download_url", ""),
        "file_name": source.get("file_name", ""),
        "remote_folder": source.get("remote_folder"),
        "metadata": dict(source.get("metadata") or {}),
    }


def _build_artifacts(upload_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for item in upload_results:
        source = item.get("source") or {}
        download = item.get("download") or {}
        artifacts.append(
            {
                "ok": bool(item.get("ok")),
                "reason": item.get("reason", ""),
                "site": source.get("site") or download.get("site") or "",
                "page_url": source.get("page_url", ""),
                "declared_download_url": source.get("download_url", ""),
                "actual_download_url": download.get("source_url", ""),
                "file_name": source.get("file_name") or download.get("file_name") or "",
                "remote_folder": source.get("remote_folder") or download.get("remote_folder"),
                "location": item.get("location", ""),
            }
        )
    return artifacts


def _route_failure(event: dict[str, Any], reason: str, task_id: str | None, app) -> dict[str, Any]:
    attempt = int(event.get("attempt", 0))
    next_attempt = attempt + 1

    base = dict(event)
    base["attempt"] = next_attempt
    base["last_error"] = reason
    base["updated_at"] = _utc_now_iso()
    if task_id:
        base["task_id"] = task_id

    if attempt < _max_retries():
        app.send_task(
            TASK_PARSE_CREATED,
            args=[base],
            queue=_retry_queue_key(),
            routing_key=_retry_queue_key(),
            serializer="json",
        )
        return {
            "state": "retried",
            "task_id": base.get("task_id"),
            "attempt": next_attempt,
            "reason": reason,
        }
    return {
        "state": "failed",
        "task_id": base.get("task_id"),
        "attempt": next_attempt,
        "reason": reason,
    }


def _schedule_source_pipelines(
    event: dict[str, Any],
    task_id: str,
    parsed_sources: list[ParsedSource],
    target: str,
    destination: str,
    app,
    service=None,
) -> dict[str, Any] | None:
    if not hasattr(app, "signature"):
        upload_results = []
        for source in parsed_sources:
            packet = process_download_source_logic(
                event=event,
                task_id=task_id,
                source=asdict(source),
                service=service,
            )
            upload_results.append(
                process_upload_result_logic(
                    download_packet=packet,
                    task_id=task_id,
                    target=target,
                    destination=destination,
                    service=service,
                )
            )
        return process_finalize_task_logic(
            upload_results=upload_results,
            event=event,
            task_id=task_id,
            app=app,
            service=service,
        )

    from celery import chain, chord

    pipelines = []
    for source in parsed_sources:
        source_dict = asdict(source)
        pipelines.append(
            chain(
                app.signature(
                    TASK_DOWNLOAD_SOURCE,
                    args=[event, task_id, source_dict],
                    queue=_download_queue_for_site(source.site),
                    routing_key=_download_queue_for_site(source.site),
                ),
                app.signature(
                    TASK_UPLOAD_RESULT,
                    args=[task_id, target, destination],
                ),
            )
        )

    callback = app.signature(
        TASK_FINALIZE,
        args=[event, task_id],
        queue=_created_queue_key(),
        routing_key=_created_queue_key(),
    )
    chord(pipelines)(callback)
    return None


def process_created_event_logic(event: dict[str, Any], app, service=None) -> dict[str, Any]:
    service = service or build_core_service()

    try:
        record = service.create_task_from_event(event)
        task_id = record.task_id
        payload = record.payload

        service.repository.update_status(task_id, TaskStatus.PARSING)
        parsed_sources = service.pipeline.parser_registry.parse(payload.url)
        if not parsed_sources:
            raise ValueError("no parsed source found")
        service.repository.update_runtime_fields(
            task_id,
            sources=[_source_snapshot(asdict(source)) for source in parsed_sources],
            artifacts=[],
            last_error="",
        )

        service.repository.update_status(task_id, TaskStatus.DOWNLOADING)
        immediate_result = _schedule_source_pipelines(
            event=event,
            task_id=task_id,
            parsed_sources=parsed_sources,
            target=payload.target,
            destination=payload.destination,
            app=app,
            service=service,
        )
        if immediate_result is not None:
            return immediate_result
        return {
            "state": "queued",
            "task_id": task_id,
            "attempt": int(event.get("attempt", 0)),
            "source_count": len(parsed_sources),
        }
    except Exception as exc:
        task_id = event.get("task_id")
        if task_id:
            service.repository.update_status(task_id, TaskStatus.FAILED, str(exc))
            service.repository.update_runtime_fields(task_id, last_error=str(exc))
        return _route_failure(event, str(exc), task_id=task_id, app=app)


def process_download_source_logic(
    event: dict[str, Any],
    task_id: str,
    source: dict[str, Any],
    service=None,
    owner_node: str | None = None,
) -> dict[str, Any]:
    service = service or build_core_service()
    service.repository.update_status(task_id, TaskStatus.DOWNLOADING)
    parsed = ParsedSource(**source)
    resolved_owner = _normalize_owner_node(owner_node) or _resolve_owner_node()
    try:
        download = service.pipeline.downloader_registry.download(parsed)
        return {
            "ok": True,
            "download": asdict(download),
            "source": _source_snapshot(source),
            "owner_node": resolved_owner,
            "task_id": task_id,
            "event": event,
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "source": _source_snapshot(source),
            "owner_node": resolved_owner,
            "task_id": task_id,
            "event": event,
        }


def process_upload_result_logic(
    download_packet: dict[str, Any], task_id: str, target: str, destination: str, service=None
) -> dict[str, Any]:
    service = service or build_core_service()
    if not download_packet.get("ok"):
        return download_packet

    service.repository.update_status(task_id, TaskStatus.UPLOADING)
    download = DownloadResult(**download_packet["download"])
    try:
        upload = service.pipeline.uploader_registry.upload(target, download, destination)
        return {
            "ok": True,
            "location": upload.location,
            "download": download_packet.get("download"),
            "source": download_packet.get("source"),
            "task_id": task_id,
            "event": download_packet.get("event"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "download": download_packet.get("download"),
            "source": download_packet.get("source"),
            "task_id": task_id,
            "event": download_packet.get("event"),
        }


def process_finalize_task_logic(upload_results: list[dict[str, Any]], event: dict[str, Any], task_id: str, app, service=None) -> dict:
    service = service or build_core_service()
    artifacts = _build_artifacts(upload_results)
    failed = [item for item in upload_results if not item.get("ok")]
    if failed:
        reason = failed[0].get("reason", "failed")
        service.repository.update_status(task_id, TaskStatus.FAILED, reason)
        service.repository.update_runtime_fields(task_id, artifacts=artifacts, last_error=reason)
        return _route_failure(event, reason, task_id=task_id, app=app)

    locations = [item["location"] for item in upload_results if item.get("location")]
    message = locations[0] if len(locations) == 1 else "\n".join(locations)
    service.repository.update_status(task_id, TaskStatus.SUCCEEDED, message=message)
    service.repository.update_runtime_fields(task_id, artifacts=artifacts, last_error="")
    return {
        "state": "succeeded",
        "task_id": task_id,
        "attempt": int(event.get("attempt", 0)),
        "result_count": len(locations),
        "message": message,
    }


celery_app = build_celery_app()

if celery_app is not None:

    @celery_app.task(name=TASK_PARSE_CREATED)
    def process_created_event(event: dict[str, Any]) -> dict[str, Any]:
        return process_created_event_logic(event=event, app=celery_app)

    @celery_app.task(name=TASK_DOWNLOAD_SOURCE, bind=True)
    def process_download_source(self, event: dict[str, Any], task_id: str, source: dict[str, Any]) -> dict[str, Any]:
        # Resolve node on worker side so upload can be routed back to the same host.
        return process_download_source_logic(
            event=event,
            task_id=task_id,
            source=source,
            owner_node=_resolve_owner_node(),
        )

    @celery_app.task(name=TASK_UPLOAD_RESULT)
    def process_upload_result(download_packet: dict[str, Any], task_id: str, target: str, destination: str) -> dict[str, Any]:
        return process_upload_result_logic(
            download_packet=download_packet, task_id=task_id, target=target, destination=destination
        )

    @celery_app.task(name=TASK_FINALIZE)
    def process_finalize_task(upload_results: list[dict[str, Any]], event: dict[str, Any], task_id: str) -> dict[str, Any]:
        return process_finalize_task_logic(upload_results=upload_results, event=event, task_id=task_id, app=celery_app)

else:

    def process_created_event(event: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("celery is required for process_created_event task")

    def process_download_source(event: dict[str, Any], task_id: str, source: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("celery is required for process_download_source task")

    def process_upload_result(download_packet: dict[str, Any], task_id: str, target: str, destination: str) -> dict[str, Any]:
        raise RuntimeError("celery is required for process_upload_result task")

    def process_finalize_task(upload_results: list[dict[str, Any]], event: dict[str, Any], task_id: str) -> dict[str, Any]:
        raise RuntimeError("celery is required for process_finalize_task task")
