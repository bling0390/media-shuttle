from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import time
from typing import Any

from ..enums import default_site_queue_suffixes
from ..logging import setup_logging
from ..storage.worker_registry import MongoWorkerRegistry, WorkerRegistry

logger = setup_logging()


def _created_queue_key() -> str:
    return os.getenv("MEDIA_SHUTTLE_CREATED_QUEUE_KEY", "media_shuttle:task_created")


def _retry_queue_key() -> str:
    return os.getenv("MEDIA_SHUTTLE_RETRY_QUEUE_KEY", "media_shuttle:task_retry")


def _download_queue_prefix() -> str:
    return os.getenv("MEDIA_SHUTTLE_DOWNLOAD_QUEUE_KEY", "media_shuttle:task_download")


def _upload_queue_prefix() -> str:
    return os.getenv("MEDIA_SHUTTLE_UPLOAD_QUEUE_KEY", "media_shuttle:task_upload")


def _worker_control_queue_prefix() -> str:
    return os.getenv("MEDIA_SHUTTLE_WORKER_CONTROL_QUEUE_KEY", "media_shuttle:worker_control")


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


def _bool_env(name: str, default: str = "0") -> bool:
    raw = os.getenv(name, default).strip().lower()
    return raw not in {"", "0", "false", "off", "no"}


def _upload_affinity_enabled() -> bool:
    return _bool_env("MEDIA_SHUTTLE_UPLOAD_AFFINITY", "1")


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


def _worker_registry_enabled() -> bool:
    if not _bool_env("MEDIA_SHUTTLE_WORKER_REGISTRY_ENABLED", "1"):
        return False
    backend = os.getenv("MEDIA_SHUTTLE_STORAGE_BACKEND", "memory").strip().lower()
    return backend == "mongo"


def _build_worker_registry() -> WorkerRegistry | None:
    if not _worker_registry_enabled():
        return None
    try:
        return MongoWorkerRegistry(
            mongo_uri=os.getenv("MEDIA_SHUTTLE_MONGO_URI", "mongodb://localhost:27017"),
            db_name=os.getenv("MEDIA_SHUTTLE_MONGO_DB", "media_shuttle"),
            collection_name=os.getenv("MEDIA_SHUTTLE_MONGO_WORKER_COLLECTION", "workers"),
        )
    except Exception:
        return None


def _worker_concurrency(worker_role: str) -> int:
    role_key = worker_role.strip().upper()
    role_specific = os.getenv(f"MEDIA_SHUTTLE_CORE_{role_key}_CONCURRENCY", "").strip()
    raw = role_specific or os.getenv("MEDIA_SHUTTLE_CORE_CONCURRENCY", "1")
    return max(1, int(raw))


def _worker_hostname(worker_role: str) -> str:
    role_key = worker_role.strip().upper()
    role_specific = os.getenv(f"MEDIA_SHUTTLE_CORE_{role_key}_WORKER_HOSTNAME", "").strip()
    if role_specific:
        return role_specific

    global_hostname = os.getenv("MEDIA_SHUTTLE_CORE_WORKER_HOSTNAME", "").strip()
    if global_hostname:
        return f"{global_hostname}-{worker_role}"

    return f"core-worker-{worker_role}@media-shuttle-core"


def _worker_queues(worker_role: str) -> str:
    role_key = worker_role.strip().upper()
    role_specific = os.getenv(f"MEDIA_SHUTTLE_CORE_{role_key}_WORKER_QUEUES", "").strip()
    if role_specific:
        return role_specific

    global_queues = os.getenv("MEDIA_SHUTTLE_CORE_WORKER_QUEUES", "").strip()
    if global_queues:
        return global_queues

    return generate_queue_names(worker_role)


def _worker_slot(role: str) -> dict[str, Any]:
    queues_csv = _worker_queues(role)
    return {
        "role": role,
        "hostname": _worker_hostname(role),
        "queues_csv": queues_csv,
        "queues": [item.strip() for item in queues_csv.split(",") if item.strip()],
        "concurrency": _worker_concurrency(role),
        "proc": None,
    }


def _upsert_worker(slot: dict[str, Any], registry: WorkerRegistry | None, status: str, reason: str = "") -> None:
    if registry is None:
        return
    proc = slot.get("proc")
    pid = None
    if proc is not None:
        pid = getattr(proc, "pid", None)
    exit_code = None
    if proc is not None:
        exit_code = proc.poll()
    registry.upsert_worker(
        hostname=str(slot["hostname"]),
        role=str(slot["role"]),
        queues=list(slot["queues"]),
        concurrency=int(slot["concurrency"]),
        status=status,
        node_id=_resolve_owner_node(),
        pid=pid,
        exit_code=exit_code if isinstance(exit_code, int) else None,
        reason=reason,
    )


def _install_signal_handlers(handler):
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[sig] = signal.getsignal(sig)
            signal.signal(sig, handler)
        except Exception:
            continue
    return previous


def _restore_signal_handlers(previous: dict) -> None:
    for sig, handler in previous.items():
        try:
            signal.signal(sig, handler)
        except Exception:
            continue


def _signal_reason(signum: int | None) -> str:
    if signum is None:
        return "signal=unknown"
    try:
        sig = signal.Signals(int(signum))
        return f"signal={sig.name}"
    except Exception:
        return f"signal={signum}"


def generate_parse_queue_names() -> list[str]:
    return [_retry_queue_key(), _created_queue_key()]


def generate_download_queue_names() -> list[str]:
    defaults = ",".join(default_site_queue_suffixes())
    sites = _csv_env(
        "MEDIA_SHUTTLE_SITE_QUEUE_SUFFIXES",
        defaults,
    )
    prefix = _download_queue_prefix()
    return [f"{prefix}@{site}" for site in sites]


def generate_upload_queue_names() -> list[str]:
    targets = _csv_env("MEDIA_SHUTTLE_UPLOAD_QUEUE_SUFFIXES", "RCLONE,TELEGRAM")
    prefix = _upload_queue_prefix()
    queues = [f"{prefix}@{target}" for target in targets]
    if _upload_affinity_enabled():
        owner = _resolve_owner_node()
        if owner:
            queues.extend([f"{prefix}@{target}@{owner}" for target in targets])
    return queues


def generate_control_queue_names() -> list[str]:
    prefix = _worker_control_queue_prefix()
    owner = _resolve_owner_node()
    queues = [prefix]
    if owner:
        queues.append(f"{prefix}@{owner}")
    return queues


def generate_queue_names(worker_role: str = "all") -> str:
    role = worker_role.strip().lower()

    if role == "parse":
        queues = generate_parse_queue_names()
    elif role == "download":
        queues = generate_download_queue_names()
    elif role == "upload":
        queues = generate_upload_queue_names()
    elif role == "control":
        queues = generate_control_queue_names()
    else:
        queues = [*generate_parse_queue_names(), *generate_download_queue_names(), *generate_upload_queue_names()]

    return ",".join(dict.fromkeys(queues))


def start_celery_process(
    worker_role: str | None = None,
    *,
    concurrency_override: int | None = None,
    hostname_override: str | None = None,
    queues_override: str | None = None,
) -> subprocess.Popen:
    role = (worker_role or os.getenv("MEDIA_SHUTTLE_CORE_WORKER_ROLE", "all")).strip().lower()
    concurrency = max(1, int(concurrency_override)) if concurrency_override is not None else _worker_concurrency(role)
    hostname = hostname_override or _worker_hostname(role)
    queues = queues_override or _worker_queues(role)

    logger.info(
        f"starting celery worker role={role} hostname={hostname} concurrency={concurrency} queues={queues}"
    )

    return subprocess.Popen(
        [
            "celery",
            "-A",
            "core.queue.tasks:celery_app",
            "worker",
            "--loglevel=INFO",
            "--without-gossip",
            "--pool=solo",
            f"--hostname={hostname}",
            f"--queues={queues}",
            f"--concurrency={concurrency}",
        ]
    )


def _resolve_roles() -> list[str]:
    role = os.getenv("MEDIA_SHUTTLE_CORE_WORKER_ROLE", "all").strip().lower()
    if role == "all":
        return ["parse", "download", "upload", "control"]
    return [role]


def _terminate_workers(procs: list[subprocess.Popen]) -> None:
    running = [proc for proc in procs if proc.poll() is None]
    if running:
        logger.info(f"terminating worker subprocesses count={len(running)}")
    for proc in running:
        proc.terminate()

    deadline = time.time() + 5
    while running and time.time() < deadline:
        running = [proc for proc in running if proc.poll() is None]
        if running:
            time.sleep(0.1)

    for proc in running:
        logger.warning(f"killing unresponsive worker pid={getattr(proc, 'pid', None)}")
        proc.kill()


def _wait_for_any_exit(procs: list[subprocess.Popen], on_tick=None) -> tuple[subprocess.Popen, int]:
    if len(procs) == 1:
        code = procs[0].wait()
        return procs[0], code

    last_tick = time.monotonic()

    while True:
        for proc in procs:
            code = proc.poll()
            if code is not None:
                return proc, code
        if on_tick is not None and (time.monotonic() - last_tick) >= 5:
            on_tick()
            last_tick = time.monotonic()
        time.sleep(0.2)


def run_forever() -> int:
    caught_signal: list[int] = []
    shutdown_requested = False

    def _on_signal(signum, _frame):
        caught_signal.append(int(signum))
        raise KeyboardInterrupt()

    previous_signal_handlers = _install_signal_handlers(_on_signal)
    roles = _resolve_roles()
    registry = _build_worker_registry()
    logger.info(f"core supervisor boot roles={','.join(roles)}")
    slots = [_worker_slot(role) for role in roles]
    procs: list[subprocess.Popen] = []
    for slot in slots:
        _upsert_worker(slot, registry, status="STARTING")
        proc = start_celery_process(str(slot["role"]))
        slot["proc"] = proc
        procs.append(proc)
        logger.info(
            f"worker subprocess started role={slot['role']} pid={getattr(proc, 'pid', None)} hostname={slot['hostname']}"
        )
        _upsert_worker(slot, registry, status="READY")
    try:
        exited_proc, code = _wait_for_any_exit(
            procs,
            on_tick=lambda: [_upsert_worker(slot, registry, status="READY") for slot in slots if slot["proc"].poll() is None],
        )
        for slot in slots:
            if slot["proc"] is exited_proc:
                status = "SHUTDOWN" if code == 0 else "CRASHED"
                reason = "" if code == 0 else f"exit_code={code}"
                logger.warning(
                    f"worker subprocess exited role={slot['role']} pid={getattr(exited_proc, 'pid', None)} status={status} reason={reason or 'normal_exit'}"
                )
                _upsert_worker(slot, registry, status=status, reason=reason)
                break
        return code
    except KeyboardInterrupt:
        shutdown_requested = True
        signum = caught_signal[-1] if caught_signal else None
        reason = _signal_reason(signum)
        logger.info(f"core supervisor received shutdown signal reason={reason}")
        for slot in slots:
            _upsert_worker(slot, registry, status="SHUTDOWN", reason=reason)
        if signum is None:
            return 130
        return 128 + int(signum)
    finally:
        _terminate_workers(procs)
        for slot in slots:
            proc = slot.get("proc")
            if proc is None:
                continue
            code = proc.poll()
            if shutdown_requested:
                signum = caught_signal[-1] if caught_signal else None
                _upsert_worker(slot, registry, status="SHUTDOWN", reason=_signal_reason(signum))
            elif code is None:
                _upsert_worker(slot, registry, status="SHUTDOWN")
            else:
                status = "SHUTDOWN" if code == 0 else "CRASHED"
                reason = "" if code == 0 else f"exit_code={code}"
                _upsert_worker(slot, registry, status=status, reason=reason)
        logger.info("core supervisor stopped")
        _restore_signal_handlers(previous_signal_handlers)
