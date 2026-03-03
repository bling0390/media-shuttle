from __future__ import annotations

import os
from datetime import date

from ...models import DownloadResult


def build_remote_name(download: DownloadResult) -> str:
    parts = []
    if os.getenv("MEDIA_SHUTTLE_USE_DATE_CATEGORY", "0") == "1":
        parts.append(str(date.today()))
    if download.remote_folder:
        parts.append(download.remote_folder)
    parts.append(download.file_name)
    return "/".join(parts)
