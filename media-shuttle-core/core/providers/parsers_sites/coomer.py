from __future__ import annotations

from ...enums import SourceSite
from ...models import ParsedSource
from .common import host, safe_name, segments


def is_coomer(url: str) -> bool:
    url_host = host(url)
    return "coomer" in url_host or "kemono" in url_host


def parse_coomer(url: str) -> list[ParsedSource]:
    segs = segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site=SourceSite.COOMER.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"coomer_{slug}.bin"),
            remote_folder=slug,
        )
    ]
