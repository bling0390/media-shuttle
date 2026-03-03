from __future__ import annotations

from ...models import DownloadResult, UploadResult
from .common import build_remote_name


def upload_telegram_mock(download: DownloadResult, destination: str) -> UploadResult:
    remote_name = build_remote_name(download)
    return UploadResult(location=f"telegram://{destination.rstrip('/')}/{remote_name}")


def upload_telegram_live(download: DownloadResult, destination: str) -> UploadResult:
    raise RuntimeError("telegram live upload is adapter-owned and not implemented in core provider")
