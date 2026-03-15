from __future__ import annotations

import httpx

from ...models import DownloadResult, ParsedSource
from ..parsers_sites.filester import resolve_filester_source
from ..user_agents import with_random_user_agent
from .common import download_live_generic


def download_filester_live(source: ParsedSource) -> DownloadResult:
    refreshed = _filester_refresh_source(source)
    actual_url = refreshed.download_url
    headers = with_random_user_agent({"Referer": source.page_url, "Range": "bytes=0-"})

    try:
        return download_live_generic(refreshed, headers=headers, actual_url=actual_url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {403, 404}:
            raise

    retried = _filester_refresh_source(source, force=True)
    return download_live_generic(retried, headers=headers, actual_url=retried.download_url)


def _filester_refresh_source(source: ParsedSource, force: bool = False) -> ParsedSource:
    if not force and _looks_like_filester_direct_url(source.download_url):
        return source

    refreshed = resolve_filester_source(source)
    if not refreshed:
        raise RuntimeError("failed to refresh filester download url")

    source.download_url = refreshed.download_url
    source.file_name = refreshed.file_name
    source.remote_folder = refreshed.remote_folder
    source.metadata.update(refreshed.metadata)
    return source


def _looks_like_filester_direct_url(url: str) -> bool:
    return ".filester.me/" in url and ("/d/" in url or "/v/" in url)
