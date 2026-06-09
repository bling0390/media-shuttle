from __future__ import annotations

import subprocess
from shutil import which

from ...models import DownloadResult, UploadResult
from .common import build_remote_name


def _split_remote_and_path(destination: str) -> tuple[str, str]:
    """Split a RCLONE destination of the form ``<remote>:<path>``.

    The destination may legitimately contain additional ``:`` in the path
    on some backends, so split on the first ``:`` only and treat the LHS
    as the remote name. Returns ``(remote, path)``; if the user supplied
    a bare remote (no ``:``), path is empty.
    """
    raw = (destination or "").strip()
    if not raw:
        return ("", "")
    head, sep, tail = raw.partition(":")
    if not sep:
        return (raw, "")
    return (head, tail)


def upload_rclone_mock(download: DownloadResult, destination: str) -> UploadResult:
    remote_name = build_remote_name(download)
    return UploadResult(location=f"rclone://{destination.rstrip('/')}/{remote_name}")


def upload_rclone_live(download: DownloadResult, destination: str) -> UploadResult:
    if which("rclone") is None:
        raise RuntimeError("rclone CLI is required for RCLONE live upload but was not found in PATH")

    remote_name = build_remote_name(download)
    remote, dest_path = _split_remote_and_path(destination)
    # dest_path may already have a leading slash; build_remote_name returns
    # no leading slash. Normalize to exactly one slash between them.
    dest_path = dest_path.rstrip("/")
    full_path = f"{dest_path}/{remote_name}" if dest_path else remote_name
    target = f"{remote}:{full_path}" if remote else full_path
    cmd = ["rclone", "copyto", download.local_path, target]
    subprocess.run(cmd, check=True)
    # Display path for the response. The actual rclone target we built
    # above is ``<remote>:<dest_path>/<remote_name>``; format the
    # ``location`` to match so it round-trips back to the same target.
    location_target = target if remote else remote_name
    return UploadResult(location=f"rclone://{location_target.lstrip('/')}")
