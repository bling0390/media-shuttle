from __future__ import annotations

import uuid
from dataclasses import dataclass

from .contracts import validate_create_request
from .models import CreateTaskRequest, TaskRecord, WorkerRecord, utc_now_iso
from .queue import TaskPublisher
from .repository import TaskRepository, WorkerRepository
from .utils import make_idempotency_key
from .worker_control import WorkerControl


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
            destination=request.destination,
            created_at=timestamp,
            updated_at=timestamp,
        )

        self.repository.create(record)
        self.publisher.publish_created_event(event)
        return record

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.repository.get(task_id)

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
        return {
            "spec_version": "task.created.v1",
            "task_id": task.task_id,
            "task_type": "parse_link",
            "idempotency_key": task.idempotency_key,
            "created_at": utc_now_iso(),
            "payload": {
                "url": task.url,
                "requester_id": task.requester_id,
                "target": task.target,
                "destination": task.destination,
            },
        }

    def admin_retry_action(self, mode: str, task_id: str | None = None, limit: int = 20) -> dict:
        mode_key = (mode or "failed").strip().lower()
        max_limit = max(1, min(int(limit), 200))

        retried: list[str] = []
        skipped = 0

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
            if task.status != "FAILED":
                return {
                    "mode": mode_key,
                    "task_id": task_id,
                    "accepted": False,
                    "reason": "task_not_failed",
                    "retried": 0,
                    "skipped": 1,
                }
            self.repository.update_status(task.task_id, "QUEUED", "")
            self.publisher.publish_created_event(self._build_created_event(task))
            return {
                "mode": mode_key,
                "task_id": task_id,
                "accepted": True,
                "retried": 1,
                "skipped": 0,
                "task_ids": [task.task_id],
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
            self.repository.update_status(item.task_id, "QUEUED", "")
            self.publisher.publish_created_event(self._build_created_event(item))
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
