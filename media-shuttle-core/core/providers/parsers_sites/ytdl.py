from __future__ import annotations

from ...enums import SourceSite
from ...models import ParsedSource
from .common import host, safe_name, segments


def is_ytdl(url: str) -> bool:
    url_host = host(url)
    return any(site in url_host for site in ["youtube.com", "youtu.be", "vimeo.com", "dailymotion.com"])


def parse_ytdl(url: str) -> list[ParsedSource]:
    segs = segments(url)
    slug = segs[-1] if segs else "video"
    return [
        ParsedSource(
            site=SourceSite.YTDL.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"ytdl_{slug}.mp4"),
            remote_folder=slug,
        )
    ]
