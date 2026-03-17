from __future__ import annotations

import httpx

from ...models import DownloadResult, ParsedSource
from ..parsers_sites.transfer import resolve_transfer_source
from ..user_agents import with_random_user_agent
from .common import download_live_generic


def download_transfer_live(source: ParsedSource) -> DownloadResult:
    refreshed = _transfer_refresh_source(source)
    actual_url = refreshed.download_url

    try:
        return download_live_generic(refreshed, headers=with_random_user_agent(), actual_url=actual_url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {403, 404}:
            raise

    retried = _transfer_refresh_source(source, force=True)
    return download_live_generic(retried, headers=with_random_user_agent(), actual_url=retried.download_url)


def _transfer_refresh_source(source: ParsedSource, force: bool = False) -> ParsedSource:
    if not force and _looks_like_transfer_direct_url(source.download_url):
        return source

    refreshed = resolve_transfer_source(source)
    if not refreshed:
        raise RuntimeError("failed to refresh transfer.it download url")

    source.download_url = refreshed.download_url
    source.file_name = refreshed.file_name
    source.remote_folder = refreshed.remote_folder
    source.metadata.update(refreshed.metadata)
    return source


def _looks_like_transfer_direct_url(url: str) -> bool:
    return "userstorage.mega.co.nz/dl/" in url
