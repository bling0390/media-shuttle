from __future__ import annotations

import re
from urllib.parse import urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from ..user_agents import with_random_user_agent
from .common import host, http_text, safe_name, segments


def is_cyberdrop(url: str) -> bool:
    return "cyberdrop" in host(url)


def is_cyberdrop_album(url: str) -> bool:
    return is_cyberdrop(url) and "/a/" in urlparse(url).path.lower()


def parse_cyberdrop(url: str) -> list[ParsedSource]:
    segs = segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site=SourceSite.CYBERDROP.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"cyberdrop_{slug}.bin"),
            remote_folder=slug,
        )
    ]


def parse_cyberdrop_album_live(url: str) -> list[ParsedSource]:
    try:
        parsed = urlparse(url)
        album = segments(url)[-1] if segments(url) else "album"
        html = http_text(url, headers=with_random_user_agent())
        links = re.findall(r'href=["\'](/f/[^"\']+)["\']', html)
        if not links:
            return []
        return [
            ParsedSource(
                site=SourceSite.CYBERDROP.value,
                page_url=f"{parsed.scheme}://{parsed.netloc}{link}",
                download_url=f"{parsed.scheme}://{parsed.netloc}{link}",
                file_name=safe_name(f"cyberdrop_{link.split('/')[-1]}.bin"),
                remote_folder=album,
            )
            for link in links
        ]
    except Exception:
        return []
