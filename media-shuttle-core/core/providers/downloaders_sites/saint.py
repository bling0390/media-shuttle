from __future__ import annotations

from ...models import DownloadResult, ParsedSource
from .common import download_live_generic
from ..user_agents import with_random_user_agent


def download_saint_live(source: ParsedSource) -> DownloadResult:
    return download_live_generic(source, headers=with_random_user_agent())
