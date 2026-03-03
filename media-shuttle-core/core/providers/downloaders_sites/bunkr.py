from __future__ import annotations

import re
from urllib.parse import urlparse

from ...models import DownloadResult, ParsedSource
from .common import download_live_generic, is_direct_file_url
from ..parsers_sites.common import guess_filename_from_path, http_text, safe_name
from ..user_agents import with_random_user_agent


def _resolve_bunkr_actual_url(source: ParsedSource) -> str:
    parsed = urlparse(source.download_url)
    path = parsed.path.lower()
    if is_direct_file_url(source.download_url):
        return source.download_url
    if "/f/" not in path and "/v/" not in path:
        return source.download_url

    html = http_text(
        source.download_url,
        headers=with_random_user_agent({"Referer": source.page_url}),
    )

    # Bunkr pages may embed direct media links in src/href/json fields.
    candidates = re.findall(r"https?://[^\s\"'<>]+", html)
    for raw in candidates:
        candidate = raw.replace("\\/", "/")
        if is_direct_file_url(candidate):
            return candidate
    return source.download_url


def _resolve_bunkr_file_name(source: ParsedSource, actual_url: str) -> str:
    if source.file_name.strip():
        return source.file_name

    slug = source.metadata.get("slug") or source.download_url.rstrip("/").split("/")[-1] or "unknown"
    if is_direct_file_url(actual_url):
        return guess_filename_from_path(actual_url, fallback=safe_name(f"bunkr_{slug}.mp4"))
    return safe_name(source.remote_folder or "", fallback=safe_name(f"bunkr_{slug}.mp4"))


def download_bunkr_live(source: ParsedSource) -> DownloadResult:
    headers = with_random_user_agent({"Referer": source.page_url, "Range": "bytes=0-"})
    actual_url = _resolve_bunkr_actual_url(source)
    resolved_file_name = _resolve_bunkr_file_name(source, actual_url)
    source_for_download = ParsedSource(
        site=source.site,
        page_url=source.page_url,
        download_url=source.download_url,
        file_name=resolved_file_name,
        remote_folder=source.remote_folder,
        metadata=source.metadata,
    )
    return download_live_generic(source_for_download, headers=headers, actual_url=actual_url)
