from __future__ import annotations

import base64
import re
from urllib.parse import quote as urllib_quote, urljoin, urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from ..user_agents import with_random_user_agent
from .common import host, http_json, http_text, is_direct_file_url, safe_name, segments


def is_bunkr(url: str) -> bool:
    return "bunkr" in host(url)


def is_bunkr_album(url: str) -> bool:
    return is_bunkr(url) and "/a/" in urlparse(url).path.lower()


def parse_bunkr(url: str) -> list[ParsedSource]:
    segs = segments(url)
    slug = segs[-1] if segs else "unknown"
    folder_name = slug
    return [
        ParsedSource(
            site=SourceSite.BUNKR.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(folder_name, fallback=f"bunkr_{slug}.mp4"),
            remote_folder=folder_name,
            metadata={"slug": slug},
        )
    ]


def parse_bunkr_live(url: str) -> list[ParsedSource]:
    fallback_slug = _bunkr_slug(url)
    fallback_folder = fallback_slug
    try:
        html = http_text(url, headers=_bunkr_headers())
    except Exception:
        return []

    folder_name = _bunkr_folder_name(html, fallback=fallback_folder)
    path = urlparse(url).path.lower()

    if "/a/" in path:
        return _parse_bunkr_album_page(url, html, folder_name)
    if "/v/" in path:
        return _parse_bunkr_single_page(url, html, folder_name)
    if "/f/" in path:
        return _parse_bunkr_single_page(url, html, folder_name)
    return []


def parse_bunkr_album_live(url: str) -> list[ParsedSource]:
    return parse_bunkr_live(url)


def _parse_bunkr_album_page(url: str, html: str, folder_name: str) -> list[ParsedSource]:
    """Resolve every child /f/<slug> link to a real CDN URL.

    The album page lists its files as ``/f/<slug>`` pages (each one is
    itself a bunkr page that hides the real CDN URL behind the
    /api/_001_v2 + glb-apisign flow). If we hand the downloader the bare
    ``/f/<slug>`` URL it will try to "find a .mp4 in the page" and pick
    the thumbnail image, downloading a tiny preview instead of the
    actual file. Pre-resolving every link keeps the downloader honest.
    """
    links = _bunkr_collect_media_links(url, html, max_depth=1)
    if not links:
        return []

    results: list[ParsedSource] = []
    for link in links:
        try:
            child_html = http_text(link, headers=_bunkr_headers(referer=url))
        except Exception:
            # If a single child fails (404, network, anti-bot) we still
            # emit a source pointing at the page URL so it surfaces as a
            # structured failure rather than silently disappearing.
            results.append(
                _bunkr_source(
                    page_url=link,
                    download_url=link,
                    remote_folder=folder_name,
                    file_name="",
                )
            )
            continue

        direct = _bunkr_resolve_single_file_download_url(link, child_html)
        if not direct:
            results.append(
                _bunkr_source(
                    page_url=link,
                    download_url=link,
                    remote_folder=folder_name,
                    file_name="",
                )
            )
            continue

        child_title = _bunkr_folder_name(child_html, fallback=folder_name)
        # Title typically has ".mp4" (or other ext) on the end; strip it
        # to use as a per-file sub-folder. Falling back to album folder
        # keeps things grouped if the title is missing.
        child_folder = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", child_title).strip() or folder_name
        child_file = _bunkr_file_name(child_html, fallback=child_title)
        results.append(
            _bunkr_source(
                page_url=link,
                download_url=direct,
                remote_folder=child_folder,
                file_name=child_file,
            )
        )
    return results


def _parse_bunkr_single_page(url: str, html: str, folder_name: str) -> list[ParsedSource]:
    direct_url = _bunkr_resolve_single_file_download_url(url, html)
    if not direct_url:
        return []
    # The page title usually includes the file extension (e.g. "Foo.mp4").
    # Use the title (without the .<ext> suffix) as the album/folder name and
    # the original upload filename (`ogname`) as the file name. Both are
    # HTML-entity-decoded by their extractors.
    folder = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", folder_name).strip() or folder_name
    file_name = _bunkr_file_name(html, fallback=folder_name)
    return [
        _bunkr_source(
            page_url=url,
            download_url=direct_url,
            remote_folder=folder,
            file_name=file_name,
        )
    ]


def _bunkr_headers(referer: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer
    return with_random_user_agent(headers)


def _bunkr_slug(url: str) -> str:
    segs = segments(url)
    return segs[-1] if segs else "unknown"


def _bunkr_folder_name(html: str, fallback: str = "album") -> str:
    patterns = [
        r'<h1[^>]*class=["\'][^"\']*\btruncate\b[^"\']*["\'][^>]*>(.*?)</h1>',
        r'<h1[^>]*class=["\'][^"\']*\btext-\[20px\]\b[^"\']*["\'][^>]*>(.*?)</h1>',
        r'<h1[^>]*class=["\'][^"\']*\btext-\[24px\]\b[^"\']*["\'][^>]*>(.*?)</h1>',
        r'<title>(?:Download\s+)?(.*?)</title>',
    ]
    for pattern in patterns:
        matched = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if not matched:
            continue
        raw = re.sub(r"<[^>]+>", " ", matched.group(1))
        # Decode HTML entities like &amp; -> & so the title matches what the
        # user sees on the page (and what bunkr's "ogname" var carries).
        try:
            import html as _html_lib

            unescaped = _html_lib.unescape(raw)
        except Exception:
            unescaped = raw
        cleaned = re.sub(r"\s+", " ", unescaped).strip()
        # The <title> for single-file pages usually looks like
        # "Download Foo.mp4" - drop the redundant prefix.
        cleaned = re.sub(r"^download\s+", "", cleaned, flags=re.IGNORECASE)
        if cleaned:
            return cleaned
    return fallback


def _bunkr_extract_hrefs(url: str, html: str) -> list[str]:
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    links: list[str] = []
    for href in hrefs:
        abs_url = urljoin(url, href.strip())
        parsed = urlparse(abs_url)
        if parsed.scheme not in {"http", "https"}:
            continue
        links.append(abs_url)
    return list(dict.fromkeys(links))


def _bunkr_is_media_page_path(path: str) -> bool:
    return bool(re.search(r"/[fv]/[^/]+/?$", path.lower()))


def _bunkr_is_album_page_path(path: str) -> bool:
    return bool(re.search(r"/a/[^/]+/?$", path.lower()))


def _bunkr_decrypt_link(encrypted_url: str, timestamp: int) -> str:
    try:
        char_codes = list(bytes(base64.b64decode(encrypted_url)))
        key = f"SECRET_KEY_{int(timestamp) // 3600}".encode("utf-8")
        decoded = bytearray(char_codes[idx] ^ key[idx % len(key)] for idx in range(len(char_codes)))
        return decoded.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _bunkr_collect_media_links(url: str, html: str, depth: int = 0, max_depth: int = 1, visited: set[str] | None = None) -> list[str]:
    visited = visited or set()
    normalized = url.rstrip("/")
    if normalized in visited:
        return []
    visited.add(normalized)

    links: list[str] = []
    for candidate in _bunkr_extract_hrefs(url, html):
        path = urlparse(candidate).path.lower()
        if is_direct_file_url(candidate) or _bunkr_is_media_page_path(path):
            links.append(candidate)
            continue

        if depth < max_depth and _bunkr_is_album_page_path(path) and is_bunkr(candidate):
            try:
                nested = http_text(candidate, headers=_bunkr_headers(referer=url))
            except Exception:
                continue
            links.extend(_bunkr_collect_media_links(candidate, nested, depth=depth + 1, max_depth=max_depth, visited=visited))

    return list(dict.fromkeys(links))


def _bunkr_file_name(html: str, fallback: str = "file.bin") -> str:
    # The wrapper page defines `var ogname = "Real Filename.mp4";` which is
    # the original filename as uploaded by the user. Prefer it over deriving
    # from the title (which has the .mp4 extension too but is less reliable).
    matched = re.search(
        r'var\s+ogname\s*=\s*["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )
    if matched:
        try:
            import html as _html_lib

            unescaped = _html_lib.unescape(matched.group(1)).strip()
        except Exception:
            unescaped = matched.group(1).strip()
        if unescaped:
            return unescaped
    return fallback


def _bunkr_origin_from_url(url: str) -> str:
    """Return scheme+netloc of a URL (used to build API endpoints)."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _bunkr_file_id_from_html(html: str) -> str:
    """Extract the numeric bunkr file id from the page wrapper.

    The bunkr.cr (or .si/.la/etc.) page embeds:

        <script defer data-file-id="57930011" src="../js/lv.js"></script>

    while the dl.bunkr.cr download wrapper embeds:

        <a id="download-btn" data-id="57930011" ...>

    The numeric id is the key to the click flow that yields a real CDN URL.
    """
    matched = re.search(
        r'data-(?:file-)?id=["\'](\d{3,})["\']',
        html,
        flags=re.IGNORECASE,
    )
    return matched.group(1).strip() if matched else ""


def _bunkr_resolve_single_file_download_url(url: str, html: str) -> str:
    # Primary path (current bunkr behavior, 2026-06): the page exposes a
    # numeric file id (data-file-id / data-id). The real CDN URL is hidden
    # behind a 2-step API flow that the click handler in dl.bunkr.cr's
    # wrapper page performs:
    #   1. POST {dl-host}/api/_001_v2  body={"id": <file_id>}
    #      -> { mediafiles, path, original }
    #   2. GET https://glb-apisign.cdn.cr/sign?path=<path>
    #      -> { token, ex }
    #   3. final = "<mediafiles><path>?token=<token>&ex=<ex>&n=<urlencoded original>"
    file_id = _bunkr_file_id_from_html(html)
    if file_id:
        signed = _bunkr_sign_via_api(file_id, source_url=url, source_html=html)
        if signed:
            return signed

    # Legacy path 1: bunkr used to embed a video/<source> tag.
    matched = re.search(
        r'<video[^>]*id=["\']player["\'][^>]*>.*?<source[^>]*src=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if matched:
        return urljoin(url, matched.group(1).strip())

    # Legacy path 2: dl.bunkr.* /f/<slug> wrapper may still embed
    #   <script data-domain="get.bunkrr.su" data-v="<uuid>.mp4">
    # but get.bunkrr.su has been observed to be unreachable (connection
    # refused). Kept as a best-effort fallback in case the mirror still
    # serves it.
    cdn_uuid = re.search(
        r'data-domain=["\']get\.bunkrr\.su["\'][^>]*data-v=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )
    if cdn_uuid:
        return f"https://get.bunkrr.su/v/{cdn_uuid.group(1).strip()}"

    slug = _bunkr_slug(url)
    parsed = urlparse(url)
    path = parsed.path.lower()
    endpoint_prefix = f"{parsed.scheme}://{parsed.netloc}"

    # Old /v/<slug> pages used to expose /api/gimmeurl. The endpoint has
    # been observed to 404 on current mirrors; kept as a best-effort legacy
    # path.
    if "/v/" in path:
        try:
            payload = http_json(
                f"{endpoint_prefix}/api/gimmeurl",
                headers={
                    "Content-Type": "application/json",
                    **_bunkr_headers(referer=url),
                },
                method="POST",
                body={"slug": slug},
            )
            new_url = str(payload.get("data", {}).get("newUrl", "")).strip()
            if new_url:
                return new_url
        except Exception:
            pass

    # /f/<slug> used to expose /api/vs with an XOR-encrypted URL. That API
    # has also been observed to 404. Removed; the new code path above
    # (file_id -> _001_v2 -> sign) supersedes it.

    download_link = re.search(
        r'<a[^>]*class=["\'][^"\']*\bic-download-01\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )
    if not download_link:
        return ""

    intermediate = urljoin(url, download_link.group(1).strip())
    if is_direct_file_url(intermediate):
        return intermediate
    try:
        nested_html = http_text(intermediate, headers=_bunkr_headers(referer=url))
    except Exception:
        return ""
    # The nested page is now the dl.bunkr.* wrapper, which carries a
    # data-id="<file_id>". Run the click flow on it.
    nested_file_id = _bunkr_file_id_from_html(nested_html)
    if nested_file_id:
        signed = _bunkr_sign_via_api(nested_file_id, source_url=intermediate, source_html=nested_html)
        if signed:
            return signed
    nested_link = re.search(
        r'<a[^>]*class=["\'][^"\']*\bic-download-01\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\']',
        nested_html,
        flags=re.IGNORECASE,
    )
    if nested_link:
        return urljoin(intermediate, nested_link.group(1).strip())
    return ""


def _bunkr_sign_via_api(file_id: str, source_url: str, source_html: str) -> str:
    """Replicate the dl.bunkr.cr click handler to obtain a signed CDN URL.

    Returns the final signed URL on success, or "" on any failure (the
    caller falls back to the next resolution strategy).
    """
    # Find the dl.bunkr.<tld> download wrapper. It's the page that hosts
    # data-id="<file_id>"; the source url we already have is either the
    # bunkr.cr/f/<slug> page (no dl. host) or the wrapper itself. If the
    # source isn't on dl.bunkr.* we need to fetch the wrapper from the
    # matching dl host - we discover it from the original page's <a>
    # ic-download-01 href, when present.
    wrapper_url = source_url
    parsed = urlparse(source_url)
    if not parsed.netloc.startswith("dl."):
        matched = re.search(
            r'href=["\']([^"\']*dl\.bunkr\.[a-z]+/[^"\']+)["\']',
            source_html,
            flags=re.IGNORECASE,
        )
        if not matched:
            return ""
        wrapper_url = urljoin(source_url, matched.group(1).strip())

    try:
        meta = http_json(
            f"{_bunkr_origin_from_url(wrapper_url).rstrip('/')}/api/_001_v2",
            headers={
                "Content-Type": "application/json",
                "Origin": f"{parsed.scheme}://{parsed.netloc}",
                "Referer": wrapper_url,
                **_bunkr_headers(referer=source_url),
            },
            method="POST",
            body={"id": file_id},
        )
    except Exception:
        return ""

    mediafiles = str(meta.get("mediafiles") or "").strip()
    media_path = str(meta.get("path") or "").strip()
    original = str(meta.get("original") or "").strip()
    if not mediafiles or not media_path:
        return ""

    try:
        sign = http_json(
            "https://glb-apisign.cdn.cr/sign?path=" + (urllib_quote(media_path, safe="")),
            headers={**_bunkr_headers(referer=wrapper_url)},
        )
    except Exception:
        return ""
    token = str(sign.get("token") or "").strip()
    ex = str(sign.get("ex") or "").strip()
    if not token or not ex:
        return ""

    final = f"{mediafiles.rstrip('/')}{media_path}?token={token}&ex={ex}"
    if original:
        final += f"&n={urllib_quote(original, safe='')}"
    return final


def _bunkr_source(
    page_url: str,
    download_url: str,
    remote_folder: str | None,
    file_name: str | None = None,
) -> ParsedSource:
    slug = _bunkr_slug(page_url)
    resolved_file_name = file_name if file_name is not None else safe_name(remote_folder or "", fallback=f"bunkr_{slug}.mp4")
    return ParsedSource(
        site=SourceSite.BUNKR.value,
        page_url=page_url,
        download_url=download_url,
        file_name=resolved_file_name,
        remote_folder=remote_folder,
        metadata={"slug": slug},
    )
