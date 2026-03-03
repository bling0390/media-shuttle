from __future__ import annotations

import subprocess
from shutil import which

from ...models import DownloadResult, UploadResult
from .common import build_remote_name


def upload_rclone_mock(download: DownloadResult, destination: str) -> UploadResult:
    remote_name = build_remote_name(download)
    return UploadResult(location=f"rclone://{destination.rstrip('/')}/{remote_name}")


def upload_rclone_live(download: DownloadResult, destination: str) -> UploadResult:
    if which("rclone") is None:
        raise RuntimeError("rclone CLI is required for RCLONE live upload but was not found in PATH")

    remote_name = build_remote_name(download)
    cmd = ["rclone", "copyto", download.local_path, f"{destination}:{remote_name}"]
    subprocess.run(cmd, check=True)
    return UploadResult(location=f"rclone://{destination.rstrip('/')}/{remote_name}")
