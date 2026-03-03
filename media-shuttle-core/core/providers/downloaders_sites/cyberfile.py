from __future__ import annotations

from ...models import DownloadResult, ParsedSource
from .common import download_live_generic
from ..user_agents import with_random_user_agent


def download_cyberfile_live(source: ParsedSource) -> DownloadResult:
    headers = with_random_user_agent({"Referer": source.page_url})
    return download_live_generic(source, headers=headers)
