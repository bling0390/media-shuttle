from __future__ import annotations

from ...models import DownloadResult, ParsedSource


def download_unsupported_live(source: ParsedSource) -> DownloadResult:
    raise RuntimeError(f"live downloader is not implemented for site: {source.site}")
