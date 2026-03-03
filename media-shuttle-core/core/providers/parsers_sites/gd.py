from __future__ import annotations

from ...enums import SourceSite
from ...models import ParsedSource
from .common import extract_drive_id, host, safe_name


def is_gd(url: str) -> bool:
    return "drive.google.com" in host(url)


def parse_gd(url: str) -> list[ParsedSource]:
    file_id = extract_drive_id(url)
    if not file_id:
        return []
    return [
        ParsedSource(
            site=SourceSite.GD.value,
            page_url=url,
            download_url=f"https://drive.usercontent.google.com/download?id={file_id}&export=download&authuser=0",
            file_name=safe_name(f"gd_{file_id}.bin"),
            remote_folder=file_id,
            metadata={"file_id": file_id},
        )
    ]
