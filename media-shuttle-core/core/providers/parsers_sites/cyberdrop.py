from __future__ import annotations

import re
from urllib.parse import urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from ..user_agents import with_random_user_agent
from .common import host, http_json, http_text, safe_name, segments

# Cyberdrop exposes a signed-URL endpoint on a separate sub-domain so we
# never have to follow the DDG-protected page through. Reference:
# https://github.com/Cyberdrop-DL/cyberdrop-dl/blob/main/cyberdrop_dl/crawlers/cyberdrop.py
# (CyberdropAPI.ENTRYPOINT = https://api.cyberdrop.cr/api, file_auth
# returns {"url": "https://<cdn>/..."} for /f/<id>).
_CYBERDROP_API_BASE = "https://api.cyberdrop.cr/api"
_CYBERDROP_HOSTS = ("cyberdrop",)  # matcher via substring on netloc


def is_cyberdrop(url: str) -> bool:
    return any(token in host(url) for token in _CYBERDROP_HOSTS)


def is_cyberdrop_album(url: str) -> bool:
    return is_cyberdrop(url) and "/a/" in urlparse(url).path.lower()


def _cyberdrop_file_id(url: str) -> str | None:
    """Extract the file id from a /f/<id> or /e/<id> URL path."""
    segs = segments(url)
    if len(segs) >= 2 and segs[0].lower() in {"f", "e"}:
        return segs[1]
    # /api/file/d/<id> form (direct download link).
    if len(segs) >= 4 and segs[0].lower() == "api" and segs[1].lower() == "file":
        return segs[-1]
    return None


def _cyberdrop_album_id(url: str) -> str | None:
    segs = segments(url)
    if len(segs) >= 2 and segs[0].lower() == "a":
        return segs[1]
    return None


def _cyberdrop_request_signed_url(file_id: str) -> str | None:
    """Hit ``GET /file/auth/<file_id>`` and return the signed CDN URL.

    Returns ``None`` if the file id is unknown (404) or the auth endpoint
    is down. A 500 with ``{"error": "..."}`` is treated the same way:
    the upstream doesn't have a real signed URL for this id.
    """
    api_url = f"{_CYBERDROP_API_BASE}/file/auth/{file_id}"
    try:
        payload = http_json(api_url, headers=with_random_user_agent())
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    signed = payload.get("url")
    return signed if isinstance(signed, str) and signed else None


def _cyberdrop_request_file_info(file_id: str) -> dict | None:
    """Hit ``GET /file/info/<file_id>`` to get the file's display name.

    The info endpoint is best-effort: a 404 or 500 means we keep the
    raw id as the file_name and let the downloader figure the rest out.
    """
    api_url = f"{_CYBERDROP_API_BASE}/file/info/{file_id}"
    try:
        payload = http_json(api_url, headers=with_random_user_agent())
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _cyberdrop_resolve_single_file(url: str, file_id: str) -> ParsedSource | None:
    """Resolve a single /f/<id> URL to a ParsedSource with a signed CDN URL."""
    info = _cyberdrop_request_file_info(file_id) or {}
    signed_url = _cyberdrop_request_signed_url(file_id)
    if not signed_url:
        return None
    raw_name = info.get("name") if isinstance(info.get("name"), str) else None
    file_name = safe_name(raw_name or file_id, fallback=f"cyberdrop_{file_id}.bin")
    return ParsedSource(
        site=SourceSite.CYBERDROP.value,
        page_url=url,
        download_url=signed_url,
        file_name=file_name,
        remote_folder=raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None,
        metadata={"file_id": file_id, "resolved_live": True},
    )


def parse_cyberdrop(url: str) -> list[ParsedSource]:
    segs = segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site=SourceSite.CYBERDROP.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"cyberdrop_{slug}.bin"),
            remote_folder=slug,
        )
    ]


def parse_cyberdrop_live(url: str) -> list[ParsedSource]:
    """Single-file ``/f/<id>`` or ``/e/<id>`` URL → signed CDN URL via the auth API."""
    file_id = _cyberdrop_file_id(url)
    if not file_id:
        return []
    source = _cyberdrop_resolve_single_file(url, file_id)
    return [source] if source else []


def parse_cyberdrop_album_live(url: str) -> list[ParsedSource]:
    """Album ``/a/<id>`` URL → one ParsedSource per child file, each pointing at a
    signed CDN URL.

    The album page is a regular HTML page (DDG-protected but `http_text`
    gets a 200 with a body that contains ``href="/f/<id>"`` per file).
    For each child we then call the auth API to upgrade the page URL to
    a signed CDN URL; if the auth call fails for a child we fall back to
    the page URL so the user can still retry by hand later.
    """
    try:
        parsed = urlparse(url)
        album_id = _cyberdrop_album_id(url) or "album"
        html = http_text(url, headers=with_random_user_agent({"Referer": url}))
    except Exception:
        return []
    links = re.findall(r'href=["\'](/f/[^"\']+)["\']', html)
    if not links:
        return []
    sources: list[ParsedSource] = []
    for link in links:
        child_id = link.rsplit("/", 1)[-1]
        page_url = f"{parsed.scheme}://{parsed.netloc}{link}"
        resolved = _cyberdrop_resolve_single_file(page_url, child_id)
        if resolved is None:
            # Fall back to the page URL so the row is still visible in
            # the artifacts list; the downloader will surface the
            # auth failure as a `reason` on the artifact.
            resolved = ParsedSource(
                site=SourceSite.CYBERDROP.value,
                page_url=page_url,
                download_url=page_url,
                file_name=safe_name(f"cyberdrop_{child_id}.bin"),
                remote_folder=album_id,
                metadata={"file_id": child_id, "resolved_live": False},
            )
        else:
            # Carry the album id so the 115 path nests under the album.
            resolved.remote_folder = album_id
        sources.append(resolved)
    return sources
