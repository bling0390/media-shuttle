from __future__ import annotations

import hashlib
import json
import os
import re
import socket
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from ..bootstrap import build_core_service
from ..enums import SourceSite, TaskStatus
from ..logging import setup_logging
from ..models import DownloadResult, ParsedSource
from ..utils import cleanup_local_download
from ..storage.worker_registry import MongoWorkerRegistry
from .celery_app import build_celery_app
from .worker_control_runtime import apply_worker_control

try:
    from celery.signals import celeryd_after_setup, worker_ready, worker_shutdown
except Exception:  # pragma: no cover - celery optional in local dev
    celeryd_after_setup = None
    worker_ready = None
    worker_shutdown = None

TASK_PARSE_CREATED = "core.queue.tasks.process_created_event"
TASK_PARSE_FORUM_THREAD = "core.queue.tasks.process_forum_thread"
TASK_DOWNLOAD_SOURCE = "core.queue.tasks.process_download_source"
TASK_UPLOAD_RESULT = "core.queue.tasks.process_upload_result"
TASK_FINALIZE = "core.queue.tasks.process_finalize_task"
TASK_WORKER_CONTROL = "core.queue.tasks.apply_worker_control"


def _forum_status_to_enum(status: str):
    """Map the forum dispatcher's string status to a TaskStatus enum.

    The forum dispatcher (``ForumDispatchResult.status``) uses
    plain strings so the layer can be tested without importing
    the core enum. The repository, however, insists on
    ``TaskStatus`` instances (it calls ``.value`` to serialize
    into mongo). The mapping is centralized here so the
    dispatcher can stay enum-agnostic.
    """
    if status == "SUCCEEDED_WITH_NO_OUTPUT":
        # Reuse SUCCEEDED — the *message* field on the record
        # carries the "no links" detail. Adding a new enum
        # value would be a wider change touching the api
        # status response schema and the operator dashboard.
        return TaskStatus.SUCCEEDED
    if status == "FAILED":
        return TaskStatus.FAILED
    if status == "SUCCEEDED":
        return TaskStatus.SUCCEEDED
    # Default to QUEUED for anything we don't recognize;
    # this is unreachable in practice (dispatcher only
    # returns the three values above) but it keeps the
    # helper total.
    return TaskStatus.QUEUED

logger = setup_logging()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _created_queue_key() -> str:
    return os.getenv("MEDIA_SHUTTLE_CREATED_QUEUE_KEY", "media_shuttle:task_created")


def _forum_thread_queue_key() -> str:
    """Queue key consumed by :func:`process_forum_thread_logic`.

    Kept separate from ``task_created`` so the parse worker
    never accidentally picks up a forum-dispatcher event
    (the events look similar — both are ``task.created.v1`` —
    and the worker that should handle them differs).
    """
    return os.getenv("MEDIA_SHUTTLE_FORUM_THREAD_QUEUE_KEY", "media_shuttle:task_forum_thread")


def _notification_queue_key() -> str:
    return os.getenv("MEDIA_SHUTTLE_NOTIFICATION_QUEUE_KEY", "media_shuttle:event_task_completed")


def _publish_task_completed_event(payload: dict[str, Any]) -> None:
    """Push a ``task.completed`` event to the notification queue.

    This is best-effort: the task is already ``SUCCEEDED`` in mongo
    by the time we get here, so a publish failure must not roll
    the finalize back. The downstream subscriber (currently the
    telegram bot) is the only consumer and is expected to be
    idempotent — redelivery after a crash replays the message
    but the user only sees a duplicate notification, never a
    missing one.
    """
    import json
    import redis
    try:
        client = redis.Redis.from_url(_redis_url())
    except Exception as exc:  # pragma: no cover
        logger.warning(f"notification publish skipped: redis init failed reason={exc}")
        return
    try:
        client.rpush(_notification_queue_key(), json.dumps(payload))
    except Exception as exc:
        logger.warning(f"notification publish failed task_id={payload.get('task_id')} reason={exc}")


def _redis_url() -> str:
    return os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0")


def _retry_queue_key() -> str:
    return os.getenv("MEDIA_SHUTTLE_RETRY_QUEUE_KEY", "media_shuttle:task_retry")


def _download_queue_prefix() -> str:
    return os.getenv("MEDIA_SHUTTLE_DOWNLOAD_QUEUE_KEY", "media_shuttle:task_download")


def _worker_control_queue_prefix() -> str:
    return os.getenv("MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY", "media_shuttle:worker_control")


def _worker_control_queue_for_node(node_id: str | None = None) -> str:
    node = _normalize_owner_node(node_id) if node_id is not None else _resolve_owner_node()
    if node:
        return f"{_worker_control_queue_prefix()}@{node}"
    return _worker_control_queue_prefix()


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


def _worker_registry_enabled() -> bool:
    backend = os.getenv("MEDIA_SHUTTLE_STORAGE_BACKEND", "memory").strip().lower()
    enabled = os.getenv("MEDIA_SHUTTLE_WORKER_REGISTRY_ENABLED", "1").strip().lower()
    return backend == "mongo" and enabled not in {"", "0", "false", "off", "no"}


_WORKER_REGISTRY = None


def _worker_registry():
    global _WORKER_REGISTRY
    if _WORKER_REGISTRY is not None:
        return _WORKER_REGISTRY
    if not _worker_registry_enabled():
        _WORKER_REGISTRY = False
        return None
    try:
        _WORKER_REGISTRY = MongoWorkerRegistry(
            mongo_uri=os.getenv("MEDIA_SHUTTLE_MONGO_URI", "mongodb://localhost:27017"),
            db_name=os.getenv("MEDIA_SHUTTLE_MONGO_DB", "media_shuttle"),
            collection_name=os.getenv("MEDIA_SHUTTLE_MONGO_WORKER_COLLECTION", "workers"),
        )
        return _WORKER_REGISTRY
    except Exception:
        _WORKER_REGISTRY = False
        return None


def _role_from_hostname(hostname: str) -> str:
    value = (hostname or "").strip().lower()
    match = re.search(r"core-worker-([a-z]+)", value)
    if not match:
        return ""
    return match.group(1)


def _queue_names_from_worker_instance(instance) -> list[str]:
    queues: list[str] = []
    try:
        queue_map = instance.app.amqp.queues
        if hasattr(queue_map, "keys"):
            for key in queue_map.keys():
                if isinstance(key, str):
                    queues.append(key)
    except Exception:
        return []
    return list(dict.fromkeys(queues))


def _touch_worker_registry(*, hostname: str, status: str, role: str, queues: list[str], concurrency: int) -> None:
    registry = _worker_registry()
    if registry is None:
        return
    try:
        registry.upsert_worker(
            hostname=hostname,
            role=role,
            queues=queues,
            concurrency=max(1, int(concurrency)),
            status=status,
            node_id=_resolve_owner_node(),
        )
    except Exception:
        return


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
        logger.warning(
            f"task retry scheduled task_id={base.get('task_id')} attempt={next_attempt} reason={reason}"
        )
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
    logger.error(f"task failed permanently task_id={base.get('task_id')} attempt={next_attempt} reason={reason}")
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
    task_hint = event.get("task_id", "")
    logger.info(f"parse task received task_id={task_hint or '-'}")

    try:
        record = service.create_task_from_event(event)
        task_id = record.task_id
        payload = record.payload

        service.repository.update_status(task_id, TaskStatus.PARSING)
        parsed_sources = service.pipeline.parser_registry.parse(payload.url)
        if not parsed_sources:
            raise ValueError("no parsed source found")

        # Per-file dedupe: drop sources that have already
        # been uploaded within the configured TTL. We do
        # this after parsing so the operator gets one
        # notification per skipped file (rather than a
        # silent gap), and *before* the mongo
        # ``update_runtime_fields`` so the stored
        # ``sources`` array matches the work that was
        # actually scheduled.
        new_sources: list[ParsedSource] = []
        skipped_sources: list[dict[str, Any]] = []
        for source in parsed_sources:
            source_dict = asdict(source)
            existing = _dedupe_lookup(source_dict)
            if existing is None:
                new_sources.append(source)
                continue
            skipped_sources.append(
                {
                    "source": source_dict,
                    "location": str(existing.get("location") or ""),
                    "previous_task_id": str(existing.get("task_id") or ""),
                }
            )
        if skipped_sources:
            logger.info(
                f"dedupe skipped task_id={task_id} skipped={len(skipped_sources)} "
                f"new={len(new_sources)}"
            )

        service.repository.update_runtime_fields(
            task_id,
            sources=[_source_snapshot(asdict(source)) for source in parsed_sources],
            artifacts=[],
            last_error="",
        )

        # When every source was deduplicated, the task
        # has no work to do. Mark it SUCCEEDED and emit a
        # per-file skipped notification for each
        # dedupe hit, so the operator's chat shows the
        # same shape as a successful album (one row
        # per file, ⏭️ instead of ✅) and the
        # ``/monitor`` count does not hang in
        # ``DOWNLOADING`` until a timeout.
        if not new_sources:
            service.repository.update_status(
                task_id,
                TaskStatus.SUCCEEDED,
                message=f"all {len(skipped_sources)} source(s) dedupe-skipped",
            )
            for entry in skipped_sources:
                _publish_dedupe_skip_notification(
                    task_id=task_id,
                    source=entry["source"],
                    location=entry["location"],
                    service=service,
                )
            return {
                "state": "succeeded",
                "task_id": task_id,
                "attempt": int(event.get("attempt", 0)),
                "source_count": 0,
                "skipped_count": len(skipped_sources),
                "message": "all sources dedupe-skipped",
            }

        service.repository.update_status(task_id, TaskStatus.DOWNLOADING)
        # Skipped sources still need a notification so
        # the operator sees the file was processed
        # (just not re-downloaded). Emit before fanning
        # out so the timeline reads naturally: skip →
        # upload, even when uploads are async.
        for entry in skipped_sources:
            _publish_dedupe_skip_notification(
                task_id=task_id,
                source=entry["source"],
                location=entry["location"],
                service=service,
            )
        immediate_result = _schedule_source_pipelines(
            event=event,
            task_id=task_id,
            parsed_sources=new_sources,
            target=payload.target,
            destination=payload.destination,
            app=app,
            service=service,
        )
        if immediate_result is not None:
            return immediate_result
        logger.info(f"parse task queued downstream pipelines task_id={task_id} source_count={len(parsed_sources)}")
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
        logger.exception(f"parse task failed task_id={task_id or '-'} reason={exc}")
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
    logger.info(
        f"download started task_id={task_id} site={parsed.site} file_name={parsed.file_name} owner_node={resolved_owner or '-'}"
    )
    try:
        download = service.pipeline.downloader_registry.download(parsed)
        logger.info(
            f"download finished task_id={task_id} site={parsed.site} local_path={download.local_path}"
        )
        return {
            "ok": True,
            "download": asdict(download),
            "source": _source_snapshot(source),
            "owner_node": resolved_owner,
            "task_id": task_id,
            "event": event,
        }
    except Exception as exc:
        logger.warning(f"download failed task_id={task_id} site={parsed.site} reason={exc}")
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
        logger.warning(
            f"upload skipped because download failed task_id={task_id} target={target} reason={download_packet.get('reason', '')}"
        )
        # Still notify on download failure so the operator
        # sees per-file errors in real time (e.g. bunkr 404
        # on one of the 247 files in an album). Without
        # this, an album with mixed success/failure only
        # surfaces the failure via the (delayed) finalize
        # summary, which feels silent to the operator.
        _publish_per_source_notification(
            download_packet=download_packet,
            task_id=task_id,
            target=target,
            ok=False,
            location="",
            reason=str(download_packet.get("reason") or ""),
            service=service,
            phase="download",
        )
        return download_packet

    service.repository.update_status(task_id, TaskStatus.UPLOADING)
    download = DownloadResult(**download_packet["download"])
    logger.info(
        f"upload started task_id={task_id} target={target} destination={destination} file_name={download.file_name}"
    )
    try:
        upload = service.pipeline.uploader_registry.upload(target, download, destination)
        cleanup_local_download(download.local_path)
        logger.info(f"upload finished task_id={task_id} target={target} location={upload.location}")
        result = {
            "ok": True,
            "location": upload.location,
            "download": download_packet.get("download"),
            "source": download_packet.get("source"),
            "task_id": task_id,
            "event": download_packet.get("event"),
        }
        # Persist the (album + file_name) → location
        # mapping so a future re-leech of the same link
        # short-circuits the download + upload. We use
        # the source dict the parser produced (site,
        # remote_folder, file_name) as the identity
        # basis, not the downloaded file path, so the
        # dedupe survives bunkr mirror domain changes.
        try:
            _dedupe_write(
                dict(download_packet.get("source") or {}),
                task_id=task_id,
                location=upload.location,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"dedupe write raised (non-fatal) task_id={task_id} reason={exc}"
            )
        _publish_per_source_notification(
            download_packet=download_packet,
            task_id=task_id,
            target=target,
            ok=True,
            location=upload.location,
            service=service,
        )
        return result
    except Exception as exc:
        logger.warning(f"upload failed task_id={task_id} target={target} reason={exc}")
        result = {
            "ok": False,
            "reason": str(exc),
            "download": download_packet.get("download"),
            "source": download_packet.get("source"),
            "task_id": task_id,
            "event": download_packet.get("event"),
        }
        _publish_per_source_notification(
            download_packet=download_packet,
            task_id=task_id,
            target=target,
            ok=False,
            location="",
            reason=str(exc),
            service=service,
            phase="upload",
        )
        return result


# ---------------------------------------------------------------------------
# Dedupe store
# ---------------------------------------------------------------------------
#
# Backs the per-file dedupe feature: if the same album + file name has
# been successfully uploaded within ``MEDIA_SHUTTLE_DEDUPE_TTL_DAYS``
# (default 30), re-leeching the same link short-circuits the
# download + upload for that file and reuses the previous
# ``rclone`` location. The dedupe key is
# ``md5(site|remote_folder|file_name)`` so the same physical file is
# deduplicated across bunkr mirror domains, cdn / page URL changes,
# and operator boundaries.
#
# Storage: Redis. We picked redis over mongo because the read path
# is hot (every parse hits the dedupe once per source) and the TTL
# is short enough that the on-disk size stays bounded by the
# upload rate. A mongo collection would also work but the indexes
# would need the same compound key, and TTL cleanup is friendlier
# in redis (``SETEX``).
#
# Failure modes: every redis call is wrapped in try/except so a
# redis hiccup does not roll back a task. On a redis read failure
# we *fail open* — treat the source as not-deduplicated and let the
# normal download proceed. Better to upload twice than to silently
# drop a file the operator wanted.


def _dedupe_ttl_seconds() -> int:
    """Read ``MEDIA_SHUTTLE_DEDUPE_TTL_DAYS`` from the
    environment with a 30-day default.

    Returns seconds so the redis ``SETEX`` call accepts it
    directly. A misconfigured non-int env value falls back
    to 30 days so an operator typo never accidentally
    disables dedupe (a 0-TTL key would expire
    immediately, defeating the whole feature).
    """
    raw = os.getenv("MEDIA_SHUTTLE_DEDUPE_TTL_DAYS", "30").strip()
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = 30
    if days < 1:
        days = 30
    return days * 86400


def _dedupe_redis_url() -> str:
    """Reuse the operator-configured redis URL.

    Falls back to the same default the notifier uses so
    core can run in dev environments that only set
    ``MEDIA_SHUTTLE_REDIS_URL`` for the bot.
    """
    return os.getenv("MEDIA_SHUTTLE_REDIS_URL", "redis://localhost:6379/0")


def _dedupe_key_for(source: dict[str, Any]) -> str:
    """Build the md5 key used to look up a previously-uploaded
    version of this file.

    Inputs (in priority order):
      1. ``site`` + ``remote_folder`` + ``file_name`` — the
         full triple. This is what the user described
         (album + file name) and is the most stable
         identifier: bunkr mirror domains all resolve to
         the same folder on the upstream, and the file
         name is the file's own identity.
      2. ``site`` + ``page_url`` + ``file_name`` — for
         parsers that do not populate ``remote_folder``
         (single-file pages on pixeldrain, gd, etc.).
         Falling back to the page URL keeps a single
         file deduplicated across repeat-leech but
         cross-domain dedup is not possible without
         the folder.

    Components are joined with a non-printable separator
    (NUL, ``\x00``) so a hostile album name like
    ``"a|b"`` cannot collide with a different file
    whose components hash to the same string.
    """
    site = str(source.get("site") or "").strip().lower()
    folder = source.get("remote_folder")
    if folder is not None and str(folder).strip():
        identity = f"{site}\x00{str(folder).strip()}\x00{str(source.get('file_name') or '').strip()}"
    else:
        identity = f"{site}\x00{str(source.get('page_url') or '').strip()}\x00{str(source.get('file_name') or '').strip()}"
    return "media_shuttle:dedupe:" + hashlib.md5(identity.encode("utf-8")).hexdigest()


def _dedupe_lookup(source: dict[str, Any]) -> dict[str, Any] | None:
    """Return the stored dedupe record for this source, or
    ``None`` on miss / on any redis failure.

    The record is the JSON we wrote in ``_dedupe_write``:
    ``{"task_id": ..., "location": ..., "uploaded_at": ...}``.
    A malformed entry (someone wrote raw text into the
    key) is treated as a miss so a stray
    ``redis-cli SET media_shuttle:dedupe:foo bar`` cannot
    brick the dedupe path forever.
    """
    key = _dedupe_key_for(source)
    try:
        import redis

        client = redis.Redis.from_url(_dedupe_redis_url())
        raw = client.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"dedupe lookup failed key={key} reason={exc}")
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("location"):
        return None
    return data


def _dedupe_write(source: dict[str, Any], task_id: str, location: str) -> None:
    """Persist the (key → location) mapping so future
    re-leeches of the same file can short-circuit.

    Called from ``process_upload_result_logic`` on the
    *success* path only — a failed upload must never
    enter the dedupe store, otherwise the next attempt
    would see a phantom "already uploaded" entry with
    no real file behind it.

    Best-effort: a redis hiccup here only loses the
    future dedupe hit for this file; the current upload
    has already finished, so we log and move on rather
    than rolling back the result.
    """
    if not location:
        return
    key = _dedupe_key_for(source)
    payload = json.dumps(
        {
            "task_id": task_id,
            "location": location,
            "uploaded_at": _utc_now_iso(),
        }
    )
    try:
        import redis

        client = redis.Redis.from_url(_dedupe_redis_url())
        client.set(key, payload, ex=_dedupe_ttl_seconds())
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"dedupe write failed key={key} reason={exc}")


def _publish_dedupe_skip_notification(
    task_id: str,
    source: dict[str, Any],
    location: str,
    service=None,
) -> None:
    """Push a per-file ``task.completed`` event for a
    dedupe-skipped source so the operator's tg chat and
    the per-file artifact list both show the file as
    handled (not silently dropped).

    The shape mirrors ``_publish_per_source_notification``
    so the tg notifier renders it through the same code
    path — only ``ok`` is ``True`` (we *did* get the file
    to 115, just via a previous run) and ``phase`` is
    ``"dedupe"`` so the retry button does not appear
    (there is nothing to retry; the previous run was
    already a success).

    ``requester_id`` resolution matches the per-source
    path: prefer the in-scope event, fall back to mongo
    on the rare case where the event payload was
    stripped by an intermediate redis hop.
    """
    requester_id = ""
    try:
        from ..queue.contracts import get_request_meta  # type: ignore

        requester_id = ""
    except Exception:
        requester_id = ""
    # Read requester from the task record; cheaper than
    # threading the event payload through here.
    if not requester_id and service is not None:
        try:
            doc = service.repository.get(task_id)
            if doc is not None:
                doc_payload = getattr(doc, "payload", None)
                if doc_payload is not None:
                    requester_id = str(
                        getattr(doc_payload, "requester_id", "") or ""
                    ).strip()
        except Exception:
            pass
    if not requester_id:
        logger.info(
            f"dedupe skip notification skipped (no requester_id) task_id={task_id}"
        )
        return
    file_name = str(source.get("file_name") or "")
    if not file_name:
        logger.info(
            f"dedupe skip notification skipped (no file_name) task_id={task_id}"
        )
        return
    size_bytes = 0  # dedupe records don't store size
    source_site = str(source.get("site") or "")
    _publish_task_completed_event(
        {
            "task_id": task_id,
            "requester_id": requester_id,
            "file_name": file_name,
            "size_bytes": size_bytes,
            "location": location or "",
            "source_site": source_site,
            "duration_seconds": 0,
            "ok": True,
            "target": "RCLONE",
            "reason": "",
            "phase": "dedupe",
            "spec_version": "task.completed.v1",
        }
    )


def _publish_per_source_notification(
    download_packet: dict[str, Any],
    task_id: str,
    target: str,
    ok: bool,
    location: str,
    reason: str = "",
    service=None,
    phase: str | None = None,
) -> None:
    """Push a per-file ``task.completed`` event to the tg subscriber.

    Called from ``process_upload_result_logic`` after every
    individual upload (success or failure) so the operator
    sees progress in real time on big albums. The finalize
    callback in ``process_finalize_task_logic`` no longer
    publishes a summary; per-file notifications are the
    only signal the tg bot needs.

    ``phase`` is set on failure paths so the tg bot can
    render the correct retry button:

    * ``"download"`` — the source could not be fetched.
      Operator can re-trigger the download phase.
    * ``"upload"`` — the file is on local disk, but the
      uploader (rclone / telegram) refused it. Operator
      can re-trigger the upload phase.

    On success the field is omitted; the bot does not show
    a retry button when the file made it through.

    Best-effort: the redis publish is wrapped in its own
    try/except so a redis hiccup never rolls the upload
    result back. The task is already terminal in mongo
    by the time this is called.
    """
    source_event = download_packet.get("event") or {}
    requester_id = str(
        ((source_event or {}).get("payload") or {}).get("requester_id") or ""
    ).strip()
    if not requester_id:
        # Fall back to the requester_id stored on the parent
        # task record. Fan-out children inherit the parent's
        # requester_id via mongo when the parent is created,
        # so this should resolve in practice even if the
        # event payload was stripped (e.g. an event that
        # came in via redis without the payload key).
        if service is not None:
            try:
                task_doc = service.repository.get(task_id)
                if task_doc is not None:
                    payload = getattr(task_doc, "payload", None)
                    if payload is not None:
                        requester_id = str(
                            getattr(payload, "requester_id", "") or ""
                        ).strip()
            except Exception:
                pass
    if not requester_id:
        logger.info(
            f"per-source notification skipped (no requester_id) task_id={task_id}"
        )
        return

    download = download_packet.get("download") or {}
    source = download_packet.get("source") or {}
    # Prefer ``download.file_name`` (set on success); fall
    # back to ``source.file_name`` so download failures —
    # where the download packet has no ``download`` key —
    # still get a useful notification. Without this fallback
    # every bunkr 410 / 404 in an album would silently
    # skip the per-file alert.
    file_name = str(
        download.get("file_name") or source.get("file_name") or ""
    )
    size_bytes = int(download.get("size_bytes") or 0)
    source_site = str(download.get("site") or source.get("site") or "")
    if not file_name:
        logger.info(
            f"per-source notification skipped (no file_name) task_id={task_id}"
        )
        return

    # Per-file duration: the source event's created_at is the
    # parent task's created_at (the fan-out child doesn't get
    # its own timestamp), so duration here is the wall-clock
    # since the parent started. For multi-file albums that's
    # the cumulative time, which is what the operator wants
    # to see ("this file took 4h 12m" end-to-end).
    duration_seconds = 0
    try:
        if source_event.get("created_at"):
            from datetime import datetime, timezone
            started = datetime.fromisoformat(
                str(source_event["created_at"]).replace("Z", "+00:00")
            )
            duration_seconds = max(
                0,
                int((datetime.now(timezone.utc) - started).total_seconds()),
            )
    except Exception:
        duration_seconds = 0

    payload: dict[str, Any] = {
        "task_id": task_id,
        "requester_id": requester_id,
        "file_name": file_name,
        "size_bytes": size_bytes,
        "location": location or "",
        "source_site": source_site,
        "duration_seconds": duration_seconds,
        "ok": ok,
        "target": target,
        "reason": reason,
        "spec_version": "task.completed.v1",
    }
    if phase:
        # The tg bot uses this to decide which inline retry
        # button to attach. ``download`` → 🔁 重试下载;
        # ``upload`` → 🔁 重试上传. Omitted on success.
        payload["phase"] = phase
    _publish_task_completed_event(payload)


def _publish_forum_fanout_events(events, service):
    """Publish each fan-out event onto ``task_created``.

    The fan-out events are *normal* ``parse_link`` events —
    the forum worker just hands them to the same queue the
    rest of the pipeline already drains. The fan-out events
    are not persisted as their own TaskRecord here: the
    downstream ``process_created_event`` will do that when
    it consumes the event. That keeps mongo writes single-
    source.

    Routing: we MUST go through ``celery_app.send_task``
    rather than a raw ``redis.rpush`` so the broker wraps
    the payload in the celery envelope (with ``properties``
    + headers). Bypassing celery — as an earlier revision
    did — produces messages that kombu's redis transport
    cannot decode (``KeyError: 'properties'`` on the
    consumer side), which crashes the parse worker the
    first time it pops one.
    """
    if not events:
        return 0
    app = _resolve_celery_app()
    if app is None:
        logger.warning("forum fanout skipped: celery app unavailable")
        return 0
    created_key = _created_queue_key()
    task_name = os.getenv(
        "MEDIA_SHUTTLE_CORE_CREATED_TASK_NAME",
        "core.queue.tasks.process_created_event",
    )
    persisted = 0
    for event in events:
        try:
            app.send_task(
                task_name,
                args=[event],
                queue=created_key,
                routing_key=created_key,
                serializer="json",
            )
            persisted += 1
        except Exception as exc:
            logger.warning(
                f"forum fanout publish failed task_id={event.get('task_id')} reason={exc}"
            )
    return persisted


def _resolve_celery_app():
    """Return the module-level celery app, or None.

    The forum fanout helper used to fall through to a raw
    redis publish when celery was unavailable, but the
    resulting messages were unparseable by the consumer.
    The current implementation explicitly requires celery
    and returns None if it isn't loaded; the caller then
    logs and bails. This makes the failure mode loud
    rather than silently corrupting the queue.
    """
    return celery_app


def process_forum_thread_logic(event, app, service=None):
    """Run a ``parse_forum_thread`` event end-to-end.

    Differences vs ``process_created_event_logic``:

    * No retry on failure (Q2: forum task failure is terminal).
    * On success, publish the fanned-out ``parse_link`` events
      back onto ``task_created`` so the existing download /
      upload pipeline picks them up unchanged.
    * The forum task record itself transitions to
      ``SUCCEEDED`` or ``SUCCEEDED_WITH_NO_OUTPUT`` with a
      short message — there are no ``artifacts`` to record
      because the *downloads* happen later in the parse_link
      tasks, not here.
    """
    service = service or build_core_service()
    task_hint = event.get("task_id", "")
    logger.info(f"forum task received task_id={task_hint or '-'}")

    try:
        record = service.create_task_from_event(event)
        task_id = record.task_id
        service.repository.update_status(task_id, TaskStatus.PARSING)

        # ``build_forum_dispatcher`` is lazy-imported to keep
        # the celery module importable in dev environments
        # that don't have lxml installed.
        from ..providers.forum import build_forum_dispatcher
        cap_env = os.environ.get("FORUM_FANOUT_CAP", "200")
        try:
            cap = int(cap_env)
        except (TypeError, ValueError):
            cap = 200
        dispatcher = build_forum_dispatcher(fanout_cap=cap)
        result = dispatcher.dispatch(event)

        service.repository.update_status(task_id, _forum_status_to_enum(result.status), message=result.message)
        service.repository.update_runtime_fields(
            task_id,
            sources=[],
            artifacts=[],
            last_error=result.last_error,
        )

        if result.status == "FAILED":
            logger.error(
                f"forum task failed task_id={task_id} reason={result.last_error}"
            )
            return {
                "state": "failed",
                "task_id": task_id,
                "reason": result.last_error or "forum dispatch failed",
            }

        if result.status == "SUCCEEDED_WITH_NO_OUTPUT":
            # ``TaskRecord`` does not expose ``requester_id``
            # as a flat attribute; it lives at
            # ``TaskRecord.payload.requester_id``. We prefer
            # the originating ``task.created.v1`` event
            # because it is always populated by the time we
            # reach this branch, and only fall back to mongo
            # for the rare case where the event is missing
            # the field.
            requester_id = str(
                ((event or {}).get("payload") or {}).get("requester_id") or ""
            ).strip()
            if not requester_id:
                first_record = service.repository.get(task_id)
                if first_record is not None:
                    payload = getattr(first_record, "payload", None)
                    if payload is not None:
                        requester_id = str(
                            getattr(payload, "requester_id", "") or ""
                        ).strip()
            if requester_id:
                _publish_task_completed_event(
                    {
                        "task_id": task_id,
                        "requester_id": requester_id,
                        "file_name": result.message,
                        "size_bytes": 0,
                        "location": "",
                        "spec_version": "task.completed.v1",
                    }
                )
            return {
                "state": "succeeded",
                "task_id": task_id,
                "source_count": 0,
                "message": result.message,
            }

        published = _publish_forum_fanout_events(
            result.fanned_out_events, service
        )
        logger.info(
            f"forum task succeeded task_id={task_id} "
            f"pages_walked={result.pages_walked} "
            f"extracted={result.extracted_links} "
            f"fanned_out={result.fanned_out_count} "
            f"published={published}"
        )
        return {
            "state": "succeeded",
            "task_id": task_id,
            "pages_walked": result.pages_walked,
            "extracted": result.extracted_links,
            "fanned_out": result.fanned_out_count,
            "message": result.message,
        }
    except Exception as exc:
        task_id = event.get("task_id")
        if task_id:
            try:
                service.repository.update_status(task_id, TaskStatus.FAILED, str(exc))
                service.repository.update_runtime_fields(task_id, last_error=str(exc))
            except Exception:
                pass
            # The forum dispatcher does not fan out per-file
            # tasks of its own (it pushes ``parse_link``
            # events into ``task_created``), so a failure
            # here would otherwise leave the operator in the
            # dark — no per-file notification fires, and the
            # only state change is the mongo ``FAILED``
            # record. Emit a task-level ``task.completed``
            # event so the tg bot can surface a retry button
            # for the whole forum scrape.
            try:
                _publish_forum_failed_notification(
                    task_id=task_id, reason=str(exc), service=service, event=event
                )
            except Exception:
                # Best-effort: a redis hiccup must not roll
                # back the task that just failed.
                logger.exception(
                    f"forum failure notification publish failed task_id={task_id}"
                )
        logger.exception(f"forum task failed task_id={task_id or '-'} reason={exc}")
        return {
            "state": "failed",
            "task_id": task_id,
            "reason": str(exc),
        }


def _publish_forum_failed_notification(
    task_id: str, reason: str, service, event: dict[str, Any]
) -> None:
    """Emit a task-level ``task.completed`` event when the
    forum dispatcher fails. Mirrors the per-file
    ``_publish_per_source_notification`` shape so the tg
    notifier can render it through the same code path.
    """
    requester_id = ""
    payload = ((event or {}).get("payload") or {}) if event else {}
    requester_id = str(payload.get("requester_id") or "").strip()
    if not requester_id and service is not None:
        try:
            doc = service.repository.get(task_id)
            if doc is not None:
                doc_payload = getattr(doc, "payload", None)
                if doc_payload is not None:
                    requester_id = str(
                        getattr(doc_payload, "requester_id", "") or ""
                    ).strip()
        except Exception:
            pass
    if not requester_id:
        logger.info(
            f"forum failure notification skipped (no requester_id) task_id={task_id}"
        )
        return
    _publish_task_completed_event(
        {
            "task_id": task_id,
            "requester_id": requester_id,
            "file_name": "[forum]",
            "size_bytes": 0,
            "location": "",
            "source_site": "forum",
            "duration_seconds": 0,
            "ok": False,
            "target": "RCLONE",
            "reason": reason,
            "phase": "forum",
            "spec_version": "task.completed.v1",
        }
    )


def process_finalize_task_logic(upload_results: list[dict[str, Any]], event: dict[str, Any], task_id: str, app, service=None) -> dict:
    service = service or build_core_service()
    artifacts = _build_artifacts(upload_results)
    failed = [item for item in upload_results if not item.get("ok")]
    if failed:
        reason = failed[0].get("reason", "failed")
        service.repository.update_status(task_id, TaskStatus.FAILED, reason)
        service.repository.update_runtime_fields(task_id, artifacts=artifacts, last_error=reason)
        logger.error(f"task finalize failed task_id={task_id} reason={reason}")
        # If the task is going to be retried, keep the local file
        # around — the next attempt will re-upload it cheaply. If
        # it's past the retry budget, the file is dead weight and
        # cleanup immediately so /tmp/media-shuttle does not fill
        # up on a long-running host. We pull the max-retries
        # budget the same way ``_route_failure`` does.
        attempt = int(event.get("attempt", 0))
        if attempt >= _max_retries():
            for item in upload_results:
                dl = (item or {}).get("download") or {}
                local_path = dl.get("local_path")
                if local_path:
                    cleanup_local_download(local_path)
        return _route_failure(event, reason, task_id=task_id, app=app)

    locations = [item["location"] for item in upload_results if item.get("location")]
    message = locations[0] if len(locations) == 1 else "\n".join(locations)
    service.repository.update_status(task_id, TaskStatus.SUCCEEDED, message=message)
    service.repository.update_runtime_fields(task_id, artifacts=artifacts, last_error="")
    logger.info(f"task finalize succeeded task_id={task_id} result_count={len(locations)}")

    # Per-file notifications are dispatched from
    # ``process_upload_result_logic`` as each upload (or
    # failure) completes. The finalize callback used to
    # publish a single summary event here, but that
    # duplicated the message for multi-file albums and
    # made the operator wait until the entire album was
    # done before seeing anything. We no longer publish
    # from finalize; mongo state + artifacts is the only
    # responsibility left at this layer.

    return {
        "state": "succeeded",
        "task_id": task_id,
        "attempt": int(event.get("attempt", 0)),
        "result_count": len(locations),
        "message": message,
    }


celery_app = build_celery_app()

if celery_app is not None:

    if celeryd_after_setup is not None:

        @celeryd_after_setup.connect
        def _on_worker_setup(sender: str, instance, **_kwargs):
            hostname = str(sender or "")
            role = _role_from_hostname(hostname)
            queues = _queue_names_from_worker_instance(instance)
            concurrency = int(getattr(instance, "concurrency", 1) or 1)
            _touch_worker_registry(
                hostname=hostname,
                status="STARTING",
                role=role,
                queues=queues,
                concurrency=concurrency,
            )

    if worker_ready is not None:

        @worker_ready.connect
        def _on_worker_ready(sender=None, **_kwargs):
            hostname = str(getattr(sender, "hostname", "") or "")
            role = _role_from_hostname(hostname)
            queues = _queue_names_from_worker_instance(sender)
            concurrency = int(getattr(sender, "concurrency", 1) or 1)
            _touch_worker_registry(
                hostname=hostname,
                status="READY",
                role=role,
                queues=queues,
                concurrency=concurrency,
            )

    if worker_shutdown is not None:

        @worker_shutdown.connect
        def _on_worker_shutdown(sender=None, **_kwargs):
            hostname = str(getattr(sender, "hostname", "") or "")
            role = _role_from_hostname(hostname)
            queues = _queue_names_from_worker_instance(sender)
            concurrency = int(getattr(sender, "concurrency", 1) or 1)
            _touch_worker_registry(
                hostname=hostname,
                status="SHUTDOWN",
                role=role,
                queues=queues,
                concurrency=concurrency,
            )

    @celery_app.task(name=TASK_PARSE_CREATED)
    def process_created_event(event: dict[str, Any]) -> dict[str, Any]:
        return process_created_event_logic(event=event, app=celery_app)

    @celery_app.task(name=TASK_PARSE_FORUM_THREAD)
    def process_forum_thread(event: dict[str, Any]) -> dict[str, Any]:
        return process_forum_thread_logic(event=event, app=celery_app)

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

    @celery_app.task(name=TASK_WORKER_CONTROL)
    def apply_worker_control_task(command: dict[str, Any]) -> dict[str, Any]:
        return apply_worker_control(command or {})

else:

    def process_created_event(event: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("celery is required for process_created_event task")

    def process_download_source(event: dict[str, Any], task_id: str, source: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("celery is required for process_download_source task")

    def process_upload_result(download_packet: dict[str, Any], task_id: str, target: str, destination: str) -> dict[str, Any]:
        raise RuntimeError("celery is required for process_upload_result task")

    def process_finalize_task(upload_results: list[dict[str, Any]], event: dict[str, Any], task_id: str) -> dict[str, Any]:
        raise RuntimeError("celery is required for process_finalize_task task")

    def apply_worker_control_task(command: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("celery is required for apply_worker_control_task")
