from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from .common import guess_filename_from_path, host, http_json, http_text, safe_name, segments

_FILESTER_ORIGIN = os.getenv("MEDIA_SHUTTLE_FILESTER_ORIGIN", "https://filester.me").rstrip("/")
_FILESTER_CDN_ORIGIN = os.getenv("MEDIA_SHUTTLE_FILESTER_CDN_ORIGIN", "https://cache1.filester.me").rstrip("/")


def is_filester(url: str) -> bool:
    hostname = host(url)
    return hostname == "filester.me" or hostname.endswith(".filester.me")


def parse_filester(url: str) -> list[ParsedSource]:
    file_slug = _filester_extract_slug(url)
    if not file_slug:
        return []
    return [
        ParsedSource(
            site=SourceSite.FILESTER.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"filester_{file_slug}.bin"),
            remote_folder=file_slug,
            metadata={"file_slug": file_slug, "resolved_live": False},
        )
    ]


def parse_filester_live(url: str) -> list[ParsedSource]:
    file_slug = _filester_extract_slug(url)
    if not file_slug:
        return []
    if _looks_like_filester_direct_url(url):
        return [
            ParsedSource(
                site=SourceSite.FILESTER.value,
                page_url=url,
                download_url=url,
                file_name=guess_filename_from_path(url, fallback=safe_name(f"filester_{file_slug}.bin")),
                remote_folder=file_slug,
                metadata={"file_slug": file_slug, "resolved_live": True},
            )
        ]

    try:
        html = http_text(url, headers={})
    except Exception:
        return parse_filester(url)

    file_name = safe_name(_filester_extract_file_name(html), fallback=f"filester_{file_slug}.bin")
    file_uuid = _filester_extract_uuid(html)
    remote_folder = file_uuid or file_slug
    return [
        ParsedSource(
            site=SourceSite.FILESTER.value,
            page_url=url,
            download_url=url,
            file_name=file_name,
            remote_folder=remote_folder,
            metadata={"file_slug": file_slug, "file_uuid": file_uuid, "resolved_live": False},
        )
    ]


def resolve_filester_source(source: ParsedSource | str) -> ParsedSource | None:
    if isinstance(source, ParsedSource):
        page_url = source.page_url
        current = source
    else:
        page_url = source
        current = None

    if current and _looks_like_filester_direct_url(current.download_url):
        return current
    if _looks_like_filester_direct_url(page_url):
        file_slug = _filester_extract_slug(page_url) or "direct"
        file_name = current.file_name if current and current.file_name.strip() else guess_filename_from_path(
            page_url, fallback=safe_name(f"filester_{file_slug}.bin")
        )
        remote_folder = current.remote_folder if current else file_slug
        metadata = dict(current.metadata) if current else {}
        metadata["resolved_live"] = True
        return ParsedSource(
            site=SourceSite.FILESTER.value,
            page_url=page_url,
            download_url=page_url,
            file_name=file_name,
            remote_folder=remote_folder,
            metadata=metadata,
        )

    file_slug = (current.metadata.get("file_slug") if current else "") or _filester_extract_slug(page_url)
    if not file_slug:
        return None

    html = ""
    try:
        html = http_text(page_url, headers={})
    except Exception:
        html = ""

    file_name = safe_name(
        (current.file_name if current else "") or _filester_extract_file_name(html),
        fallback=safe_name(f"filester_{file_slug}.bin"),
    )
    file_uuid = (current.metadata.get("file_uuid") if current else "") or _filester_extract_uuid(html)
    remote_folder = file_uuid or (current.remote_folder if current else "") or file_slug

    try:
        payload = _filester_public_download(file_slug)
    except Exception:
        return None

    download_path = str(payload.get("download_url") or "").strip()
    if not download_path:
        return None

    actual_url = _filester_build_download_url(download_path)
    metadata = dict(current.metadata) if current else {}
    metadata.update({"file_slug": file_slug, "file_uuid": file_uuid, "resolved_live": True})

    return ParsedSource(
        site=SourceSite.FILESTER.value,
        page_url=page_url,
        download_url=actual_url,
        file_name=file_name,
        remote_folder=remote_folder,
        metadata=metadata,
    )


def _filester_public_download(file_slug: str) -> dict:
    payload = http_json(
        f"{_FILESTER_ORIGIN}/api/public/download",
        headers={"Content-Type": "application/json"},
        method="POST",
        body={"file_slug": file_slug},
    )
    return payload if isinstance(payload, dict) else {}


def _filester_build_download_url(path: str) -> str:
    suffix = "&download=true" if "?" in path else "?download=true"
    return f"{_FILESTER_CDN_ORIGIN}{path}{suffix}"


def _filester_extract_slug(url: str) -> str:
    segs = segments(url)
    if len(segs) >= 2 and segs[0].lower() in {"d", "v"}:
        return segs[1]
    return segs[-1] if segs else ""


def _filester_extract_file_name(html: str) -> str:
    patterns = [
        r"<title>\s*([^<]+?)\s*\|\s*filester\.me\s*</title>",
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<h1[^>]*>\s*([^<]+?)\s*</h1>",
    ]
    for pattern in patterns:
        matched = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if matched:
            return matched.group(1).strip()
    return ""


def _filester_extract_uuid(html: str) -> str:
    matched = re.search(r'window\.fileUUID\s*=\s*"([0-9a-f-]{36})"', html, flags=re.IGNORECASE)
    return matched.group(1).strip() if matched else ""


def _looks_like_filester_direct_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = parsed.netloc.lower()
    return hostname.endswith(".filester.me") and hostname != "filester.me" and parsed.path.startswith(("/d/", "/v/"))
