from __future__ import annotations

from urllib.parse import urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from .common import host, safe_name, segments


def is_pixeldrain(url: str) -> bool:
    return "pixeldrain.com" in host(url)


def parse_pixeldrain(url: str) -> list[ParsedSource]:
    segs = segments(url)
    slug = segs[-1] if segs else "unknown"
    parsed = urlparse(url)
    path = parsed.path.lower()
    if "/u/" in path:
        download_url = f"{parsed.scheme}://{parsed.netloc}/api/file/{slug}"
    elif "/l/" in path:
        download_url = f"{parsed.scheme}://{parsed.netloc}/api/list/{slug}"
    else:
        download_url = url
    return [
        ParsedSource(
            site=SourceSite.PIXELDRAIN.value,
            page_url=url,
            download_url=download_url,
            file_name=safe_name(f"pixeldrain_{slug}.bin"),
            remote_folder=slug,
        )
    ]
