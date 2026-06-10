from __future__ import annotations

import os

from ...models import DownloadResult, ParsedSource
from .common import download_live_generic

# Fileditchfiles page body already contains the signed CDN URL in
# the ``<source src="...">`` tag. The parser promotes that URL into
# ``download_url`` (and sets ``metadata.resolved_live = True``), so
# the downloader is a thin wrapper that adds a real-browser
# ``Referer``/``User-Agent`` (the CDN gates anonymous traffic with
# adblocker bait on the landing page, but the CDN itself is fine
# for curl as long as the ``Referer`` is the landing host).
_FILEDITCHFILES_USER_AGENT = os.getenv(
    "MEDIA_SHUTTLE_FILEDITCHFILES_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
)
_FILEDITCHFILES_LANDING = os.getenv(
    "MEDIA_SHUTTLE_FILEDITCHFILES_LANDING", "https://fileditchfiles.me"
).rstrip("/")


def download_fileditchfiles_live(source: ParsedSource) -> DownloadResult:
    # The parsed source has ``download_url`` set to the signed CDN
    # URL (or the page URL as a last-ditch fallback). Either way we
    # stream from it directly with a real-browser User-Agent and
    # the landing page as Referer.
    referer = source.page_url if source.page_url.startswith(_FILEDITCHFILES_LANDING) else f"{_FILEDITCHFILES_LANDING}/"
    headers = {
        "User-Agent": _FILEDITCHFILES_USER_AGENT,
        "Referer": referer,
    }
    return download_live_generic(source, headers=headers, actual_url=source.download_url)
