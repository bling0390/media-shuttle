from __future__ import annotations

import os
import subprocess
from datetime import date

from ..enums import UploadTarget
from ..models import DownloadResult, UploadResult
from .types import UploadProvider


def _build_remote_name(download: DownloadResult) -> str:
    parts = []
    if os.getenv("MEDIA_SHUTTLE_USE_DATE_CATEGORY", "0") == "1":
        parts.append(str(date.today()))
    if download.remote_folder:
        parts.append(download.remote_folder)
    parts.append(download.file_name)
    return "/".join(parts)


def upload_rclone_mock(download: DownloadResult, destination: str) -> UploadResult:
    remote_name = _build_remote_name(download)
    return UploadResult(location=f"rclone://{destination.rstrip('/')}/{remote_name}")


def upload_telegram_mock(download: DownloadResult, destination: str) -> UploadResult:
    remote_name = _build_remote_name(download)
    return UploadResult(location=f"telegram://{destination.rstrip('/')}/{remote_name}")


def upload_rclone_live(download: DownloadResult, destination: str) -> UploadResult:
    remote_name = _build_remote_name(download)
    cmd = ["rclone", "copyto", download.local_path, f"{destination}:{remote_name}"]
    subprocess.run(cmd, check=True)
    return UploadResult(location=f"rclone://{destination.rstrip('/')}/{remote_name}")


def upload_telegram_live(download: DownloadResult, destination: str) -> UploadResult:
    raise RuntimeError("telegram live upload is adapter-owned and not implemented in core provider")


def builtin_upload_providers(mode: str) -> list[UploadProvider]:
    providers: list[UploadProvider] = []

    if mode == "live":
        providers.extend(
            [
                UploadProvider("rclone_live", "live", lambda target: target == UploadTarget.RCLONE.value, upload_rclone_live),
                UploadProvider("telegram_live", "live", lambda target: target == UploadTarget.TELEGRAM.value, upload_telegram_live),
            ]
        )

    providers.extend(
        [
            UploadProvider("rclone_mock", "mock", lambda target: target == UploadTarget.RCLONE.value, upload_rclone_mock),
            UploadProvider("telegram_mock", "mock", lambda target: target == UploadTarget.TELEGRAM.value, upload_telegram_mock),
        ]
    )
    return providers
