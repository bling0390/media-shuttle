from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from .common import guess_filename_from_path, host, http_text, safe_name, segments

# Fileditchfiles is a small free anonymous file host. The page is
# rendered as a static "File Viewer" with the real download URL
# embedded in the <source> and the "Download" button. There is no
# auth or signed-URL step — the only protection is an adblocker
# bait overlay. A real-browser User-Agent skips the bait and the
# body is fully parseable HTML.
#
# The CDN lives on a separate (cute) domain
# (``donotsharethesetemplinksyouidiot.st``); the signed query
# string carries an ``md5`` (per-file integrity token) and an
# ``expires`` (unix timestamp, ~30 days). The same path on
# either the landing or the CDN host serves the same body, so
# the parser matches the landing host and the downloader uses
# the CDN URL from the parsed HTML.
_FILEDITCHFILES_LANDING = os.getenv(
    "MEDIA_SHUTTLE_FILEDITCHFILES_LANDING", "https://fileditchfiles.me"
).rstrip("/")
_FILEDITCHFILES_USER_AGENT = os.getenv(
    "MEDIA_SHUTTLE_FILEDITCHFILES_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
)


def is_fileditchfiles(url: str) -> bool:
    hostname = host(url)
    if not hostname:
        return False
    # Fileditchfiles has historically used ``fileditchfiles.me``
    # (and the same landing path may show up on other TLDs as
    # the project re-spins mirrors). The matcher accepts any
    # ``fileditchfiles.<tld>`` plus obvious subdomains.
    return (
        hostname == "fileditchfiles.me"
        or hostname.endswith(".fileditchfiles.me")
    )


def parse_fileditchfiles(url: str) -> list[ParsedSource]:
    file_token = _fileditchfiles_extract_token(url)
    if not file_token:
        return []
    return [
        ParsedSource(
            site=SourceSite.FILEDITCHFILES.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"fileditchfiles_{file_token}.bin"),
            remote_folder=file_token,
            metadata={"file_token": file_token, "resolved_live": False},
        )
    ]


def parse_fileditchfiles_live(url: str) -> list[ParsedSource]:
    file_token = _fileditchfiles_extract_token(url)
    if not file_token:
        return []

    # If the user pasted a direct CDN URL, the page_url is the
    # file itself; we still want the real filename from the
    # path, not just the slug.
    if _looks_like_fileditchfiles_cdn_url(url):
        return [
            ParsedSource(
                site=SourceSite.FILEDITCHFILES.value,
                page_url=url,
                download_url=url,
                file_name=guess_filename_from_path(
                    url, fallback=safe_name(f"fileditchfiles_{file_token}.bin")
                ),
                remote_folder=file_token,
                metadata={"file_token": file_token, "resolved_live": True},
            )
        ]

    try:
        html = http_text(url, headers={"User-Agent": _FILEDITCHFILES_USER_AGENT})
    except Exception:
        return parse_fileditchfiles(url)

    # The file viewer renders a single <video><source src="..."> tag
    # whose ``src`` is the signed CDN URL. Prefer the CDN URL as the
    # declared ``download_url`` so the downloader can use it
    # straight away, and use the page URL as the ``page_url`` so the
    # ``Referer`` header is the landing page (required by the CDN).
    cdn_url = _fileditchfiles_extract_cdn_url(html)
    file_name = safe_name(
        _fileditchfiles_extract_file_name(html, fallback_url=cdn_url or url),
        fallback=safe_name(f"fileditchfiles_{file_token}.bin"),
    )
    declared_url = cdn_url or url
    return [
        ParsedSource(
            site=SourceSite.FILEDITCHFILES.value,
            page_url=url,
            download_url=declared_url,
            file_name=file_name,
            remote_folder=file_token,
            metadata={
                "file_token": file_token,
                "resolved_live": bool(cdn_url),
            },
        )
    ]


# ---- helpers -----------------------------------------------------------------


def _fileditchfiles_extract_token(url: str) -> str:
    """Pull the path-based file token from a fileditchfiles URL.

    Fileditchfiles paths look like ``/alpha6/<uuid>/<file>.mp4``.
    The token is the second segment, which is the canonical id
    used to group files on the landing page. Falls back to the
    last segment if the shape is unexpected.
    """
    segs = segments(url)
    if len(segs) >= 2:
        return segs[1]
    return segs[-1] if segs else ""


def _fileditchfiles_extract_cdn_url(html: str) -> str:
    """Pull the signed CDN URL from the file viewer's <source>/<a> tags."""
    patterns = [
        r'<source\s[^>]*src="([^"]+)"',
        r'<a\s[^>]*href="([^"]+)"[^>]*class="btn\s+btn-main"',
    ]
    for pattern in patterns:
        matched = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if matched:
            return (
                matched.group(1)
                .replace("&amp;", "&")
                .strip()
            )
    return ""


def _fileditchfiles_extract_file_name(html: str, fallback_url: str) -> str:
    """Best-effort display filename.

    Fileditchfiles doesn't render a clean <title>; the page title
    is literally "File Viewer". The real filename lives in the
    ``<source src=...>`` URL's last path segment, which is what
    we fall back to.
    """
    patterns = [
        r'<source\s[^>]*src="[^"]+/([^"/?#]+)\?',
        r'<a\s[^>]*download[^>]*>\s*[^<]*</a>\s*<a\s[^>]*href="[^"]+/([^"/?#]+)\?',
    ]
    for pattern in patterns:
        matched = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if matched:
            return matched.group(1).strip()
    return guess_filename_from_path(fallback_url, fallback="")


def _looks_like_fileditchfiles_cdn_url(url: str) -> bool:
    """The CDN sits on its own (cute) host; the landing page lives
    on ``fileditchfiles.me``. A direct CDN URL is ready to download
    and skips the page fetch entirely."""
    parsed = urlparse(url)
    hostname = parsed.netloc.lower()
    # Match the canonical CDN and any future mirror. The path
    # shape (``/alpha6/<token>/<file>``) is the same.
    return (
        hostname == "donotsharethesetemplinksyouidiot.st"
        or hostname.endswith(".donotsharethesetemplinksyouidiot.st")
    ) and parsed.path.startswith("/")
