from __future__ import annotations

from ...enums import SourceSite
from ...models import ParsedSource
from .common import host, safe_name, segments


def is_mega(url: str) -> bool:
    return "mega.nz" in host(url)


def parse_mega(url: str) -> list[ParsedSource]:
    segs = segments(url)
    slug = segs[-1] if segs else "file"
    return [
        ParsedSource(
            site=SourceSite.MEGA.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"mega_{slug}.bin"),
            remote_folder=slug,
        )
    ]
