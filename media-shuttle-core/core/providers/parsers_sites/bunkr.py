from __future__ import annotations

import base64
import re
from urllib.parse import urljoin, urlparse

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
    links = _bunkr_collect_media_links(url, html, max_depth=1)
    if not links:
        return []
    return [_bunkr_source(page_url=link, download_url=link, remote_folder=folder_name, file_name="") for link in links]


def _parse_bunkr_single_page(url: str, html: str, folder_name: str) -> list[ParsedSource]:
    direct_url = _bunkr_resolve_single_file_download_url(url, html)
    if not direct_url:
        return []
    return [_bunkr_source(page_url=url, download_url=direct_url, remote_folder=folder_name)]


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
    ]
    for pattern in patterns:
        matched = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if not matched:
            continue
        raw = re.sub(r"<[^>]+>", " ", matched.group(1))
        cleaned = re.sub(r"\s+", " ", raw).strip()
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


def _bunkr_resolve_single_file_download_url(url: str, html: str) -> str:
    matched = re.search(
        r'<video[^>]*id=["\']player["\'][^>]*>.*?<source[^>]*src=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if matched:
        return urljoin(url, matched.group(1).strip())

    slug = _bunkr_slug(url)
    parsed = urlparse(url)
    path = parsed.path.lower()
    endpoint_prefix = f"{parsed.scheme}://{parsed.netloc}"

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

    if "/f/" in path:
        try:
            payload = http_json(
                f"{endpoint_prefix}/api/vs",
                headers={
                    "Content-Type": "application/json",
                    **_bunkr_headers(referer=url),
                },
                method="POST",
                body={"slug": slug},
            )
            encrypted = str(payload.get("url", "")).strip()
            timestamp = int(payload.get("timestamp", 0))
            if encrypted and timestamp > 0:
                decrypted = _bunkr_decrypt_link(encrypted, timestamp)
                if decrypted:
                    return decrypted
        except Exception:
            pass

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
    nested_link = re.search(
        r'<a[^>]*class=["\'][^"\']*\bic-download-01\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\']',
        nested_html,
        flags=re.IGNORECASE,
    )
    if nested_link:
        return urljoin(intermediate, nested_link.group(1).strip())
    return ""


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
