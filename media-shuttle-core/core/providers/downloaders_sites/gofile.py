from __future__ import annotations

from ...models import DownloadResult, ParsedSource
from .common import download_live_generic
from ..user_agents import with_random_user_agent


def download_gofile_live(source: ParsedSource) -> DownloadResult:
    token = source.metadata.get("token")
    headers = with_random_user_agent()
    if token:
        headers["Cookie"] = f"accountToken={token}"
    return download_live_generic(source, headers=headers)
