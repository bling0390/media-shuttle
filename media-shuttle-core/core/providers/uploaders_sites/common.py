from __future__ import annotations

import os
import re
from datetime import date

from ...models import DownloadResult

# Characters that 115 / rclone tend to reject or misinterpret in a remote
# path. Note: `&` is intentionally NOT in this list - tested with the
# 115 backend and it accepts `&` in filenames. `?#%` break URL parsing on
# the rclone command line; `<>:\"\\|*\x00` are filesystem-forbidden on
# Windows and may also confuse 115's path parser.
_RCLONE_PATH_BAD = re.compile(r"[?#%+\[\]<>:\"\\|*\x00-\x1f]+")


def _rclone_path_safe(name: str) -> str:
    cleaned = _RCLONE_PATH_BAD.sub("_", name).strip().strip(".")
    return cleaned or "file"


def build_remote_name(download: DownloadResult) -> str:
    parts = []
    if os.getenv("MEDIA_SHUTTLE_USE_DATE_CATEGORY", "0") == "1":
        parts.append(str(date.today()))
    if download.remote_folder:
        parts.append(_rclone_path_safe(download.remote_folder))
    parts.append(_rclone_path_safe(download.file_name))
    return "/".join(parts)
