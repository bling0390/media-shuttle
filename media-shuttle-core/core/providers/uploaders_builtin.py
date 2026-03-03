from __future__ import annotations

from ..enums import UploadTarget
from .types import UploadProvider
from .uploaders_sites import upload_rclone_live, upload_rclone_mock, upload_telegram_live, upload_telegram_mock


def builtin_upload_providers(mode: str) -> list[UploadProvider]:
    providers: list[UploadProvider] = []

    if mode == "live":
        providers.extend(
            [
                UploadProvider(
                    "rclone_live", "live", lambda target: target == UploadTarget.RCLONE.value, upload_rclone_live
                ),
                UploadProvider(
                    "telegram_live",
                    "live",
                    lambda target: target == UploadTarget.TELEGRAM.value,
                    upload_telegram_live,
                ),
            ]
        )

    providers.extend(
        [
            UploadProvider(
                "rclone_mock", "mock", lambda target: target == UploadTarget.RCLONE.value, upload_rclone_mock
            ),
            UploadProvider(
                "telegram_mock",
                "mock",
                lambda target: target == UploadTarget.TELEGRAM.value,
                upload_telegram_mock,
            ),
        ]
    )
    return providers
