from __future__ import annotations

import os
import subprocess
import time

from ..enums import default_site_queue_suffixes


def _created_queue_key() -> str:
    return os.getenv("MEDIA_SHUTTLE_CREATED_QUEUE_KEY", "media_shuttle:task_created")


def _retry_queue_key() -> str:
    return os.getenv("MEDIA_SHUTTLE_RETRY_QUEUE_KEY", "media_shuttle:task_retry")


def _download_queue_prefix() -> str:
    return os.getenv("MEDIA_SHUTTLE_DOWNLOAD_QUEUE_KEY", "media_shuttle:task_download")


def _upload_queue_prefix() -> str:
    return os.getenv("MEDIA_SHUTTLE_UPLOAD_QUEUE_KEY", "media_shuttle:task_upload")


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


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
    return [f"{prefix}@{target}" for target in targets]


def generate_queue_names(worker_role: str = "all") -> str:
    role = worker_role.strip().lower()

    if role == "parse":
        queues = generate_parse_queue_names()
    elif role == "download":
        queues = generate_download_queue_names()
    elif role == "upload":
        queues = generate_upload_queue_names()
    else:
        queues = [*generate_parse_queue_names(), *generate_download_queue_names(), *generate_upload_queue_names()]

    return ",".join(dict.fromkeys(queues))


def start_celery_process(worker_role: str | None = None) -> subprocess.Popen:
    role = (worker_role or os.getenv("MEDIA_SHUTTLE_CORE_WORKER_ROLE", "all")).strip().lower()
    concurrency = _worker_concurrency(role)
    hostname = _worker_hostname(role)
    queues = _worker_queues(role)

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
        return ["parse", "download", "upload"]
    return [role]


def _terminate_workers(procs: list[subprocess.Popen]) -> None:
    running = [proc for proc in procs if proc.poll() is None]
    for proc in running:
        proc.terminate()

    deadline = time.time() + 5
    while running and time.time() < deadline:
        running = [proc for proc in running if proc.poll() is None]
        if running:
            time.sleep(0.1)

    for proc in running:
        proc.kill()


def _wait_for_any_exit(procs: list[subprocess.Popen]) -> int:
    if len(procs) == 1:
        return procs[0].wait()

    while True:
        for proc in procs:
            code = proc.poll()
            if code is not None:
                return code
        time.sleep(0.2)


def run_forever() -> int:
    roles = _resolve_roles()
    procs = [start_celery_process(role) for role in roles]
    try:
        return _wait_for_any_exit(procs)
    except KeyboardInterrupt:
        return 130
    finally:
        _terminate_workers(procs)
