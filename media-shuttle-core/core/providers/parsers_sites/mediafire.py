from __future__ import annotations

from ...enums import SourceSite
from ...models import ParsedSource
from .common import host, safe_name, segments


def is_mediafire(url: str) -> bool:
    return "mediafire.com" in host(url)


def parse_mediafire(url: str) -> list[ParsedSource]:
    segs = segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site=SourceSite.MEDIAFIRE.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"mediafire_{slug}.bin"),
            remote_folder=slug,
        )
    ]
