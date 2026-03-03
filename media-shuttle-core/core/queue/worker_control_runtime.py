from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from .worker_process import _resolve_owner_node, start_celery_process

_ALLOWED_ROLES = {"parse", "download", "upload"}
_LOCK = threading.Lock()
_PROCS: dict[str, "_ManagedProc"] = {}


@dataclass
class _ManagedProc:
    role: str
    proc: object
    concurrency: int
    started_at: float


def _normalize_node(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).upper()


def _managed_hostname(role: str) -> str:
    base = f"core-worker-{role}-managed@media-shuttle-core"
    owner = _resolve_owner_node()
    if not owner:
        return base
    return f"core-worker-{role}-managed-{owner}@media-shuttle-core"


def _record(role: str) -> _ManagedProc | None:
    slot = _PROCS.get(role)
    if slot is None:
        return None
    if slot.proc.poll() is None:
        return slot
    _PROCS.pop(role, None)
    return None


def apply_worker_control(command: dict[str, Any]) -> dict[str, Any]:
    action = str(command.get("action", "")).strip().lower()
    role = str(command.get("role", "")).strip().lower()
    node_id = _normalize_node(str(command.get("node_id", "")))
    current_node = _normalize_node(_resolve_owner_node())
    requested_concurrency = max(1, int(command.get("concurrency") or 1))

    if role not in _ALLOWED_ROLES:
        return {"accepted": False, "reason": "unsupported_role", "action": action, "role": role}
    if node_id and current_node and node_id != current_node:
        return {
            "accepted": False,
            "reason": "node_mismatch",
            "action": action,
            "role": role,
            "node_id": node_id,
            "current_node": current_node,
        }
    if action not in {"start", "stop", "restart", "status"}:
        return {"accepted": False, "reason": "unsupported_action", "action": action, "role": role}

    with _LOCK:
        slot = _record(role)

        if action == "status":
            if slot is None:
                return {"accepted": True, "action": action, "role": role, "state": "stopped"}
            return {
                "accepted": True,
                "action": action,
                "role": role,
                "state": "running",
                "pid": getattr(slot.proc, "pid", None),
                "concurrency": slot.concurrency,
            }

        if action in {"stop", "restart"} and slot is not None:
            slot.proc.terminate()
            deadline = time.time() + 10
            while slot.proc.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            if slot.proc.poll() is None:
                slot.proc.kill()
            _PROCS.pop(role, None)
            if action == "stop":
                return {"accepted": True, "action": action, "role": role, "state": "stopped"}

        if action == "start" and slot is not None:
            return {
                "accepted": True,
                "action": action,
                "role": role,
                "state": "already_running",
                "pid": getattr(slot.proc, "pid", None),
                "concurrency": slot.concurrency,
            }

        proc = start_celery_process(
            role,
            concurrency_override=requested_concurrency,
            hostname_override=_managed_hostname(role),
        )
        _PROCS[role] = _ManagedProc(
            role=role,
            proc=proc,
            concurrency=requested_concurrency,
            started_at=time.time(),
        )
        return {
            "accepted": True,
            "action": "start" if action in {"start", "restart"} else action,
            "role": role,
            "state": "starting",
            "pid": getattr(proc, "pid", None),
            "concurrency": requested_concurrency,
        }
