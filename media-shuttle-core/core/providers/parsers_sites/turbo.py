from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from .common import guess_filename_from_path, host, http_json, http_text, safe_name, segments

_TURBO_ORIGIN = os.getenv("MEDIA_SHUTTLE_TURBO_ORIGIN", "https://turbo.cr").rstrip("/")


def is_turbo(url: str) -> bool:
    hostname = host(url)
    return hostname == "turbo.cr" or hostname.endswith(".turbo.cr") or hostname.endswith(".turbocdn.st")


def parse_turbo(url: str) -> list[ParsedSource]:
    slug = _turbo_extract_slug(url)
    if not slug:
        return []
    return [
        ParsedSource(
            site=SourceSite.TURBO.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"turbo_{slug}.bin"),
            remote_folder=slug,
            metadata={"slug": slug, "resolved_live": False},
        )
    ]


def parse_turbo_live(url: str) -> list[ParsedSource]:
    slug = _turbo_extract_slug(url)
    if not slug:
        return []
    if _looks_like_turbo_direct_url(url):
        return [
            ParsedSource(
                site=SourceSite.TURBO.value,
                page_url=url,
                download_url=url,
                file_name=guess_filename_from_path(url, fallback=safe_name(f"turbo_{slug}.bin")),
                remote_folder=slug,
                metadata={"slug": slug, "resolved_live": True},
            )
        ]

    try:
        html = http_text(url, headers={})
    except Exception:
        return parse_turbo(url)

    file_name = safe_name(_turbo_extract_file_name(html), fallback=safe_name(f"{slug}.mp4"))
    return [
        ParsedSource(
            site=SourceSite.TURBO.value,
            page_url=url,
            download_url=url,
            file_name=file_name,
            remote_folder=slug,
            metadata={"slug": slug, "resolved_live": False},
        )
    ]


def resolve_turbo_source(source: ParsedSource | str) -> ParsedSource | None:
    if isinstance(source, ParsedSource):
        page_url = source.page_url
        current = source
    else:
        page_url = source
        current = None

    if current and _looks_like_turbo_direct_url(current.download_url):
        return current
    if _looks_like_turbo_direct_url(page_url):
        slug = _turbo_extract_slug(page_url) or "direct"
        file_name = current.file_name if current and current.file_name.strip() else guess_filename_from_path(
            page_url, fallback=safe_name(f"turbo_{slug}.bin")
        )
        remote_folder = current.remote_folder if current else slug
        metadata = dict(current.metadata) if current else {}
        metadata["resolved_live"] = True
        return ParsedSource(
            site=SourceSite.TURBO.value,
            page_url=page_url,
            download_url=page_url,
            file_name=file_name,
            remote_folder=remote_folder,
            metadata=metadata,
        )

    slug = (current.metadata.get("slug") if current else "") or _turbo_extract_slug(page_url)
    if not slug:
        return None

    html = ""
    try:
        html = http_text(page_url, headers={})
    except Exception:
        html = ""

    try:
        payload = _turbo_sign(slug)
    except Exception:
        return None

    direct_url = str(payload.get("url") or "").strip()
    if not direct_url:
        return None

    file_name = safe_name(
        str(payload.get("filename") or "").strip()
        or (current.file_name if current else "")
        or _turbo_extract_file_name(html),
        fallback=safe_name(f"{slug}.mp4"),
    )
    metadata = dict(current.metadata) if current else {}
    metadata.update({"slug": slug, "resolved_live": True})

    return ParsedSource(
        site=SourceSite.TURBO.value,
        page_url=page_url,
        download_url=direct_url,
        file_name=file_name,
        remote_folder=(current.remote_folder if current else None) or slug,
        metadata=metadata,
    )


def _turbo_sign(slug: str) -> dict:
    payload = http_json(f"{_TURBO_ORIGIN}/api/sign?v={slug}", headers={}, method="GET")
    return payload if isinstance(payload, dict) else {}


def _turbo_extract_slug(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.netloc.lower()
    segs = segments(url)

    if hostname.endswith(".turbocdn.st"):
        candidate = segs[-1] if segs else ""
        if candidate.endswith(".mp4"):
            candidate = candidate[:-4]
        return candidate

    if len(segs) >= 2 and segs[0].lower() in {"v", "d", "embed"}:
        return segs[1]

    candidate = segs[-1] if segs else ""
    if candidate:
        return candidate

    query_slug = parse_qs(parsed.query).get("v", [""])[0].strip()
    return query_slug


def _turbo_extract_file_name(html: str) -> str:
    patterns = [
        r"<title>\s*([^<]+?)\s*[—|-]\s*turbo\.cr\s*</title>",
        r"<h1[^>]*>\s*([^<]+?)\s*</h1>",
        r'"filename"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        matched = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if matched:
            return matched.group(1).strip()
    return ""


def _looks_like_turbo_direct_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower().endswith(".turbocdn.st") and parsed.path.startswith("/data/")
