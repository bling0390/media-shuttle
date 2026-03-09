from __future__ import annotations

import re
from urllib.parse import unquote_plus, urljoin, urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from .common import guess_filename_from_path, host, http_text, safe_name, segments


def is_mediafire(url: str) -> bool:
    return "mediafire.com" in host(url)


def parse_mediafire(url: str) -> list[ParsedSource]:
    source = _mediafire_source(page_url=url, download_url=url)
    return [source] if source else []


def parse_mediafire_live(url: str) -> list[ParsedSource]:
    return parse_mediafire(url)


def resolve_mediafire_source(url: str) -> ParsedSource | None:
    if _looks_like_mediafire_download_url(url):
        source = _mediafire_source(page_url=url, download_url=url)
        return source

    try:
        html = http_text(url, headers={})
    except Exception:
        return None

    direct_url = _mediafire_extract_download_url(url, html)
    if not direct_url:
        return None

    source = _mediafire_source(page_url=url, download_url=direct_url)
    if not source:
        return None
    source.metadata["resolved_live"] = True
    return source


def _mediafire_source(page_url: str, download_url: str) -> ParsedSource | None:
    resource_id, name = _mediafire_parts(download_url)
    if not resource_id:
        resource_id, fallback_name = _mediafire_parts(page_url)
        if not name:
            name = fallback_name

    file_name = safe_name(name, fallback=f"mediafire_{resource_id or 'unknown'}.bin")
    remote_folder = resource_id or None
    return ParsedSource(
        site=SourceSite.MEDIAFIRE.value,
        page_url=page_url,
        download_url=download_url,
        file_name=file_name,
        remote_folder=remote_folder,
        metadata={"resource_id": resource_id or "", "resolved_live": download_url != page_url},
    )


def _mediafire_parts(url: str) -> tuple[str, str]:
    segs = segments(url)
    if not segs:
        return "", ""

    lowered = [segment.lower() for segment in segs]
    if len(segs) >= 3 and lowered[0] in {"file", "view"}:
        return segs[1], _mediafire_clean_name(segs[2])

    if len(segs) >= 2:
        return segs[-2], _mediafire_clean_name(segs[-1])

    return segs[-1], guess_filename_from_path(url, fallback=safe_name(f"mediafire_{segs[-1]}.bin"))


def _mediafire_extract_download_url(page_url: str, html: str) -> str:
    patterns = [
        r'<a[^>]+id=["\']downloadButton["\'][^>]+href=["\']([^"\']+)["\']',
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]+id=["\']downloadButton["\']',
        r'<a[^>]+aria-label=["\']Download file["\'][^>]+href=["\']([^"\']+)["\']',
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*Download\s*(?:\(|<)',
    ]
    for pattern in patterns:
        matched = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if not matched:
            continue
        href = matched.group(1).strip()
        if href:
            return urljoin(page_url, href)
    return ""


def _looks_like_mediafire_download_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower().startswith("download") and "mediafire.com" in parsed.netloc.lower()


def _mediafire_clean_name(value: str) -> str:
    return unquote_plus(value).strip()
