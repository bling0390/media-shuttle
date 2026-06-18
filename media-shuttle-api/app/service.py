from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from .contracts import DEFAULT_RCLONE_DESTINATION, validate_create_request, validate_create_forum_request
from .models import CreateTaskRequest, CreateForumTaskRequest, TaskRecord, WorkerRecord, utc_now_iso
from .queue import TaskPublisher
from .repository import TaskRepository, WorkerRepository
from .utils import make_idempotency_key
from .worker_control import WorkerControl

logger = logging.getLogger(__name__)


@dataclass
class ApiService:
    repository: TaskRepository
    publisher: TaskPublisher
    worker_repository: WorkerRepository
    worker_control: WorkerControl

    def create_parse_task(self, request: CreateTaskRequest) -> TaskRecord:
        payload = {
            "url": request.url,
            "requester_id": request.requester_id,
            "target": request.target,
            "destination": request.destination,
        }
        validate_create_request(payload)

        # ``validate_create_request`` mutates ``payload["destination"]``
        # to the RCLONE default (``115:/``) when the caller didn't
        # provide one. Persist the resolved value so the task record and
        # downstream worker logs show what the upload actually targets
        # rather than the original empty string.
        resolved_destination = payload.get("destination") or DEFAULT_RCLONE_DESTINATION

        task_id = str(uuid.uuid4())
        idempotency_key = make_idempotency_key(request.url, request.requester_id)
        timestamp = utc_now_iso()

        event = {
            "spec_version": "task.created.v1",
            "task_id": task_id,
            "task_type": "parse_link",
            "idempotency_key": idempotency_key,
            "created_at": timestamp,
            "payload": payload,
        }

        record = TaskRecord(
            task_id=task_id,
            idempotency_key=idempotency_key,
            status="QUEUED",
            requester_id=request.requester_id,
            url=request.url,
            target=request.target,
            destination=resolved_destination,
            task_type="parse_link",
            created_at=timestamp,
            updated_at=timestamp,
        )

        self.repository.create(record)
        self.publisher.publish_created_event(event)
        return record

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.repository.get(task_id)

    def create_forum_thread_task(self, request: CreateForumTaskRequest) -> TaskRecord:
        """Submit a ``parse_forum_thread`` task.

        The forum task is its own event on a separate queue
        (``media_shuttle:task_forum_thread``). The
        ``task_type`` on the event tells the core worker
        which handler to invoke. The downstream fan-out
        events produced by the dispatcher are vanilla
        ``parse_link`` events, so this method only knows
        how to publish the *forum* event itself.
        """
        payload = {
            "url": request.url,
            "requester_id": request.requester_id,
            "target": request.target,
            "destination": request.destination,
            "max_pages": int(request.max_pages or 0),
        }
        validate_create_forum_request(payload)
        resolved_destination = payload.get("destination") or DEFAULT_RCLONE_DESTINATION

        task_id = str(uuid.uuid4())
        idempotency_key = make_idempotency_key(
            f"forum:{request.url}:{request.max_pages}", request.requester_id
        )
        timestamp = utc_now_iso()

        event = {
            "spec_version": "task.created.v1",
            "task_id": task_id,
            "task_type": "parse_forum_thread",
            "idempotency_key": idempotency_key,
            "created_at": timestamp,
            "payload": payload,
        }

        record = TaskRecord(
            task_id=task_id,
            idempotency_key=idempotency_key,
            status="QUEUED",
            requester_id=request.requester_id,
            url=request.url,
            target=request.target,
            destination=resolved_destination,
            task_type="parse_forum_thread",
            created_at=timestamp,
            updated_at=timestamp,
        )

        self.repository.create(record)
        self.publisher.publish_forum_event(event)
        return record

    def list_tasks(self, status: str | None, limit: int) -> list[TaskRecord]:
        return self.repository.list(status=status, limit=limit)

    def queue_stats(self) -> dict[str, int]:
        return self.repository.stats()

    def list_workers(self, status: str | None = None, limit: int = 100, refresh: bool = True) -> list[WorkerRecord]:
        max_limit = max(1, min(int(limit), 500))
        if refresh:
            for hostname, payload in self.worker_control.inspect_workers().items():
                record = self.worker_repository.get(hostname) or WorkerRecord(hostname=hostname)
                self.worker_repository.upsert(
                    WorkerRecord(
                        hostname=hostname,
                        role=str(payload.get("role") or record.role),
                        queue=str(payload.get("queue") or ",".join(payload.get("queues", [])) or record.queue),
                        queues=[q for q in payload.get("queues", record.queues) if isinstance(q, str)],
                        status=str(payload.get("status") or "READY"),
                        concurrency=max(1, int(payload.get("concurrency") or record.concurrency)),
                        desired_concurrency=max(
                            1, int(payload.get("desired_concurrency") or payload.get("concurrency") or record.desired_concurrency)
                        ),
                        node_id=str(payload.get("node_id") or record.node_id),
                        pid=payload.get("pid", record.pid),
                        exit_code=payload.get("exit_code", record.exit_code),
                        rate_limits=dict(payload.get("rate_limits") or record.rate_limits),
                        last_error=str(payload.get("last_error") or record.last_error),
                        started_at=str(payload.get("started_at") or record.started_at),
                        last_heartbeat_at=str(payload.get("last_heartbeat_at") or utc_now_iso()),
                        updated_at=str(payload.get("updated_at") or utc_now_iso()),
                    )
                )
        return self.worker_repository.list(status=status, limit=max_limit)

    def admin_worker_action(
        self,
        worker: str,
        queue: str,
        concurrency: int,
        action: str = "set",
        node_id: str = "",
        role: str = "",
    ) -> dict:
        worker = (worker or "").strip()
        queue = (queue or "").strip()
        action_key = (action or "set").strip().lower()
        role_key = (role or "").strip().lower()
        node_key = (node_id or "").strip()
        desired = int(concurrency)

        if action_key in {"start", "stop", "restart"}:
            if role_key not in {"parse", "download", "upload"}:
                return {"accepted": False, "reason": "role_required", "supported_roles": ["parse", "download", "upload"]}
            if not node_key:
                return {"accepted": False, "reason": "node_id_required"}
            out = self.worker_control.publish_control_command(
                node_id=node_key,
                role=role_key,
                action=action_key,
                concurrency=max(1, desired),
                queue=queue,
            )
            host = _managed_worker_hostname(role=role_key, node_id=node_key)
            if out.get("accepted"):
                current = self.worker_repository.get(host) or WorkerRecord(hostname=host)
                status = "STARTING" if action_key in {"start", "restart"} else "STOPPING"
                self.worker_repository.upsert(
                    WorkerRecord(
                        hostname=host,
                        role=role_key,
                        queue=current.queue,
                        queues=current.queues,
                        status=status,
                        concurrency=current.concurrency,
                        desired_concurrency=max(1, desired),
                        node_id=node_key,
                        pid=current.pid,
                        exit_code=current.exit_code,
                        rate_limits=current.rate_limits,
                        last_error=current.last_error,
                        started_at=current.started_at,
                        last_heartbeat_at=current.last_heartbeat_at,
                        updated_at=utc_now_iso(),
                    )
                )
            return {
                "accepted": bool(out.get("accepted")),
                "action": action_key,
                "role": role_key,
                "node_id": node_key,
                "queue": out.get("queue", queue),
                "concurrency": max(1, desired),
                "operation": out,
            }

        if not worker:
            return {"accepted": False, "reason": "worker_required"}

        operations: list[dict] = []
        current = self.worker_repository.get(worker) or WorkerRecord(hostname=worker)

        if action_key == "start":
            return {
                "accepted": False,
                "worker": worker,
                "action": action_key,
                "reason": "start_not_supported_use_orchestrator",
            }

        if action_key == "shutdown" or desired <= 0:
            out = self.worker_control.shutdown(worker)
            operations.append(out)
            if out.get("accepted"):
                self.worker_repository.upsert(
                    WorkerRecord(
                        hostname=worker,
                        role=current.role,
                        queue=current.queue,
                        queues=current.queues,
                        status="SHUTDOWN",
                        concurrency=current.concurrency,
                        desired_concurrency=current.desired_concurrency,
                        node_id=current.node_id,
                        pid=current.pid,
                        exit_code=current.exit_code,
                        rate_limits=current.rate_limits,
                        last_error=current.last_error,
                        started_at=current.started_at,
                        last_heartbeat_at=utc_now_iso(),
                        updated_at=utc_now_iso(),
                    )
                )
            return {
                "accepted": bool(out.get("accepted")),
                "worker": worker,
                "queue": queue,
                "concurrency": desired,
                "action": "shutdown",
                "operations": operations,
            }

        if queue:
            queue_out = self.worker_control.add_queue(worker=worker, queue=queue)
            operations.append(queue_out)
            if queue_out.get("accepted"):
                queues = list(dict.fromkeys([*current.queues, queue]))
                self.worker_repository.patch(
                    worker,
                    queue=",".join(queues),
                    queues=queues,
                    updated_at=utc_now_iso(),
                )
                current = self.worker_repository.get(worker) or WorkerRecord(hostname=worker, queues=queues, queue=",".join(queues))

        set_out = self.worker_control.set_concurrency(worker=worker, concurrency=desired)
        operations.append(set_out)
        if set_out.get("accepted"):
            latest = self.worker_repository.get(worker) or current
            self.worker_repository.upsert(
                WorkerRecord(
                    hostname=worker,
                    role=latest.role,
                    queue=latest.queue,
                    queues=latest.queues,
                    status="READY",
                    concurrency=max(1, int(set_out.get("after") or desired)),
                    desired_concurrency=max(1, desired),
                    node_id=latest.node_id,
                    pid=latest.pid,
                    exit_code=latest.exit_code,
                    rate_limits=latest.rate_limits,
                    last_error=latest.last_error,
                    started_at=latest.started_at,
                    last_heartbeat_at=latest.last_heartbeat_at,
                    updated_at=utc_now_iso(),
                )
            )
        else:
            # Persist operator intent so dashboard still shows pending desired state.
            if self.worker_repository.get(worker) is None:
                self.worker_repository.upsert(
                    WorkerRecord(
                        hostname=worker,
                        queue=current.queue,
                        queues=current.queues,
                        status=current.status or "UNKNOWN",
                        concurrency=max(1, current.concurrency),
                        desired_concurrency=max(1, desired),
                        updated_at=utc_now_iso(),
                    )
                )
            else:
                self.worker_repository.patch(worker, desired_concurrency=max(1, desired), updated_at=utc_now_iso())

        accepted = all(bool(item.get("accepted")) for item in operations) if operations else False
        return {
            "accepted": accepted,
            "worker": worker,
            "queue": queue,
            "concurrency": desired,
            "action": action_key,
            "operations": operations,
        }

    def admin_rate_limit_action(self, worker: str, task_type: str, rate_limit: str) -> dict:
        worker_key = (worker or "").strip()
        task_key = (task_type or "").strip()
        task_name = _resolve_task_name(task_key)
        if not worker_key:
            return {"accepted": False, "reason": "worker_required"}
        if not task_name:
            return {"accepted": False, "reason": "task_type_required"}
        if not rate_limit:
            return {"accepted": False, "reason": "rate_limit_required"}

        out = self.worker_control.set_rate_limit(worker=worker_key, task_name=task_name, rate_limit=rate_limit)
        if out.get("accepted"):
            current = self.worker_repository.get(worker_key) or WorkerRecord(hostname=worker_key)
            limits = dict(current.rate_limits)
            limits[task_name] = rate_limit
            self.worker_repository.upsert(
                WorkerRecord(
                    hostname=worker_key,
                    role=current.role,
                    queue=current.queue,
                    queues=current.queues,
                    status=current.status,
                    concurrency=current.concurrency,
                    desired_concurrency=current.desired_concurrency,
                    node_id=current.node_id,
                    pid=current.pid,
                    exit_code=current.exit_code,
                    rate_limits=limits,
                    last_error=current.last_error,
                    started_at=current.started_at,
                    last_heartbeat_at=current.last_heartbeat_at,
                    updated_at=utc_now_iso(),
                )
            )
        return {
            "accepted": bool(out.get("accepted")),
            "worker": worker_key,
            "task_type": task_key,
            "task_name": task_name,
            "rate_limit": rate_limit,
            "operation": out,
        }

    def _build_created_event(self, task: TaskRecord) -> dict:
        # ``task_type`` is round-tripped from the original
        # event so a forum task that was retried through
        # this path lands back on the forum queue, not the
        # single-link one. Records created before the
        # ``task_type`` column existed default to
        # ``parse_link`` on read, so legacy records still
        # get a sensible envelope.
        return {
            "spec_version": "task.created.v1",
            "task_id": task.task_id,
            "task_type": task.task_type or "parse_link",
            "idempotency_key": task.idempotency_key,
            "created_at": utc_now_iso(),
            "payload": {
                "url": task.url,
                "requester_id": task.requester_id,
                "target": task.target,
                "destination": task.destination,
            },
        }

    def admin_retry_action(
        self,
        mode: str,
        task_id: str | None = None,
        limit: int = 20,
        requester_id: str | None = None,
        is_admin: bool = False,
    ) -> dict:
        """Re-queue a single task (or batch of failed tasks).

        ``requester_id`` is checked against the task's
        ``requester_id`` when set: the operator who hit the
        inline ``🔁 重试`` button on their own task is
        allowed through, but a different operator (or a
        non-admin who guessed someone else's task_id) is
        rejected with ``reason="requester_mismatch"``. Pass
        ``is_admin=True`` to skip the check — the dashboard
        bulk-retry path needs to sweep across operators.

        Idempotency: if the task is no longer in
        ``FAILED`` (it was already retried, or it's still
        in flight), we return ``accepted=False`` with
        ``reason="task_not_failed"`` so a double-tap on the
        Telegram button cannot duplicate the event in
        redis.
        """
        mode_key = (mode or "failed").strip().lower()
        max_limit = max(1, min(int(limit), 200))

        retried: list[str] = []
        skipped = 0

        def _republish(task: TaskRecord) -> None:
            # Reset the runtime state so the freshly re-queued
            # event starts clean. ``update_status`` rewrites
            # ``status`` + ``message`` + ``updated_at``; we
            # follow up with a direct ``update_runtime_fields``
            # for ``last_error`` so the failure reason from the
            # previous run doesn't bleed into the next run's
            # logs. ``sources`` and ``artifacts`` are kept —
            # the new run will overwrite them on the first
            # status update, and the operator can still see
            # what was parsed last time while the new run is
            # in flight.
            self.repository.update_status(task.task_id, "QUEUED", "")
            self.repository.update_runtime_fields(task.task_id, last_error="")
            event = self._build_created_event(task)
            if event.get("task_type") == "parse_forum_thread":
                # Forum tasks live on their own queue so the
                # dispatcher (not the parse-link handler)
                # drains them. Republishing to
                # ``task_created`` would deadlock the worker
                # because no parse-link consumer is wired to
                # call ``process_forum_thread_logic``.
                self.publisher.publish_forum_event(event)
            else:
                self.publisher.publish_created_event(event)

        if task_id:
            task = self.repository.get(task_id)
            if task is None:
                return {
                    "mode": mode_key,
                    "task_id": task_id,
                    "accepted": False,
                    "reason": "task_not_found",
                    "retried": 0,
                    "skipped": 1,
                }
            # Re-read status after the read so a race
            # between two operators hitting the button at
            # the same instant does not cause two
            # republishes. ``update_status`` is atomic in
            # mongo (``$set`` on a single doc) so the
            # second one will land on the already-QUEUED
            # state and we just bail.
            if task.status != "FAILED":
                return {
                    "mode": mode_key,
                    "task_id": task_id,
                    "accepted": False,
                    "reason": "task_not_failed",
                    "retried": 0,
                    "skipped": 1,
                }
            if (
                requester_id
                and not is_admin
                and str(requester_id).strip() != str(task.requester_id or "").strip()
            ):
                # Reject cross-operator retries. The
                # ``task_not_found`` reason is the same one
                # we'd return for a real 404, so the message
                # doesn't leak whether the task exists
                # under a different owner.
                logger.warning(
                    "retry rejected: requester mismatch task_id=%s expected=%s got=%s",
                    task_id,
                    task.requester_id,
                    requester_id,
                )
                return {
                    "mode": mode_key,
                    "task_id": task_id,
                    "accepted": False,
                    "reason": "task_not_found",
                    "retried": 0,
                    "skipped": 1,
                }
            _republish(task)
            return {
                "mode": mode_key,
                "task_id": task_id,
                "accepted": True,
                "retried": 1,
                "skipped": 0,
                "task_ids": [task.task_id],
                "task_type": task.task_type or "parse_link",
            }

        if mode_key not in {"failed", "both"}:
            return {
                "mode": mode_key,
                "accepted": False,
                "reason": "unsupported_mode",
                "retried": 0,
                "skipped": 0,
            }

        failed_items = self.repository.list(status="FAILED", limit=max_limit)
        for item in failed_items:
            if item.status != "FAILED":
                skipped += 1
                continue
            _republish(item)
            retried.append(item.task_id)

        return {
            "mode": mode_key,
            "accepted": True,
            "retried": len(retried),
            "skipped": skipped,
            "task_ids": retried,
        }

    def admin_setting_action(self, key: str, value: str) -> dict:
        return {"key": key, "value": value, "accepted": True}

    def admin_cleanup_downloads_action(self, dry_run: bool = False) -> dict:
        """Manually wipe the local download directory.

        Backs the ``/leech cleanup`` Telegram command. The
        underlying sweep is implemented in
        ``app.cleanup.sweep_download_dir`` so it lives in the
        api image (which is the container that receives the
        ``POST /v1/admin/cleanup-downloads`` request) and does
        not require the ``core`` package to be installed
        inside the api process. Path safety mirrors
        ``core.utils.cleanup_local_download``: every candidate
        is checked against the resolved download root before
        removal, so the call can never reach outside
        ``MEDIA_SHUTTLE_DOWNLOAD_DIR``.
        """
        from .cleanup import sweep_download_dir
        return {"accepted": True, **sweep_download_dir(dry_run=bool(dry_run))}


def _resolve_task_name(task_type: str) -> str:
    mapping = {
        "parse": "core.queue.tasks.process_created_event",
        "download": "core.queue.tasks.process_download_source",
        "upload": "core.queue.tasks.process_upload_result",
        "finalize": "core.queue.tasks.process_finalize_task",
    }
    key = (task_type or "").strip().lower()
    if key in mapping:
        return mapping[key]
    if "." in task_type:
        return task_type.strip()
    return ""


def _normalize_node(raw: str) -> str:
    import re

    value = (raw or "").strip()
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).upper()


def _managed_worker_hostname(role: str, node_id: str) -> str:
    node = _normalize_node(node_id)
    return f"core-worker-{role}-managed-{node}@media-shuttle-core"
