from __future__ import annotations

import os
import re
from datetime import date

from ...models import DownloadResult

# Characters that 115 / rclone tend to reject or misinterpret in a remote
# path. `&` is the worst offender - some rclone backends treat it as a
# query separator, others store the path with the literal `&` and then
# read-back paths fail. We replace these with a single underscore.
_RCLONE_PATH_BAD = re.compile(r"[&?#%+\[\]<>:\"\\|*\x00-\x1f]+")


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
