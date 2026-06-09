from __future__ import annotations

import os
from urllib.parse import urlencode, urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from ..user_agents import with_random_user_agent
from .common import host, http_json, safe_name, segments

# Gofile API v2 base URL. Older code targeted v1 (POST /accounts, custom
# X-Website-Token / X-BL headers + urlencoded wt/bbl parameters), which
# gofile shut down in 2024. v2 uses a plain JWT bearer token + standard
# Authorization header. See https://gofile.io/api for the spec.
_GOFILE_API_BASE = "https://api.gofile.io"


def is_gofile(url: str) -> bool:
    return "gofile.io" in host(url)


def parse_gofile(url: str) -> list[ParsedSource]:
    """Mock-mode parser: emit a placeholder source keyed off the share id.

    Kept for offline tests; live mode in `parse_gofile_live` supersedes it.
    """
    segs = segments(url)
    resource_id = segs[-1] if segs else "unknown"
    remote_folder = resource_id if "/d/" in urlparse(url).path else None
    return [
        ParsedSource(
            site=SourceSite.GOFILE.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"gofile_{resource_id}.bin"),
            remote_folder=remote_folder,
            metadata={"resource_id": resource_id},
        )
    ]


def parse_gofile_live(url: str) -> list[ParsedSource]:
    """Gofile API v2 live parser.

    Flow:
        1. Extract the content id from the share URL (last path segment
           after /d/).
        2. Get a bearer token. Two options, in priority order:
            a. MEDIA_SHUTTLE_GOFILE_TOKEN env var (recommended for
               server-side automation; long-lived JWT).
            b. POST /accounts to auto-create a guest account (rate-limited
               by gofile; one token per host).
        3. GET /contents/{contentId}?cache=true with the bearer token.
        4. Recurse into child folders; emit one ParsedSource per file.
           The download_url from v2 already includes the signed CDN path
           (e.g. https://store-eu-1.gofile.io/.../<file>?token=...); the
           downloader reuses it as-is.
    """
    content_id = _gofile_extract_id(url)
    if not content_id:
        return []
    try:
        token = _gofile_get_token()
    except Exception:
        return []
    return _gofile_list_sources(content_id, token=token)


def _gofile_extract_id(url: str) -> str | None:
    path = urlparse(url).path
    segs = [item for item in path.split("/") if item]
    if not segs:
        return None
    if len(segs) >= 2 and segs[0].lower() in {"d", "contents"}:
        return segs[1]
    return segs[-1]


def _gofile_get_token() -> str:
    """Resolve a gofile v2 JWT bearer token.

    Order of preference:
        1. MEDIA_SHUTTLE_GOFILE_TOKEN env (long-lived user account token)
        2. Auto-create guest via POST /accounts (no auth required; the
           returned token is what v2 expects in `Authorization: Bearer`).
    """
    static_token = os.getenv("MEDIA_SHUTTLE_GOFILE_TOKEN", "").strip()
    if static_token:
        return static_token

    resp = http_json(
        f"{_GOFILE_API_BASE}/accounts",
        headers=with_random_user_agent(
            {
                "Accept": "*/*",
                "Connection": "keep-alive",
            }
        ),
        method="POST",
    )
    if resp.get("status") != "ok":
        raise RuntimeError(
            f"failed to create gofile guest account: status={resp.get('status')} data={resp.get('data')}"
        )
    token = str(resp.get("data", {}).get("token", "")).strip()
    if not token:
        raise RuntimeError("gofile /accounts response missing data.token")
    return token


def _gofile_list_sources(content_id: str, token: str, password: str | None = None) -> list[ParsedSource]:
    """Walk a gofile content tree and return one ParsedSource per file.

    A single /d/<id> URL can be either a file or a folder. Folders recurse;
    files are emitted with the v2 CDN link (which already includes the
    signed token in the query string). The same token is passed to the
    downloader via metadata so it can set the accountToken cookie on the
    request to the CDN host.
    """
    request_headers = with_random_user_agent(
        {
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Authorization": f"Bearer {token}",
        }
    )

    params: dict[str, str] = {"cache": "true"}
    if password:
        params["password"] = password

    resp = http_json(
        f"{_GOFILE_API_BASE}/contents/{content_id}?{urlencode(params)}",
        headers=request_headers,
        method="GET",
    )
    if resp.get("status") != "ok":
        return []

    data = resp.get("data") or {}
    folder_name = data.get("name")

    if data.get("type") == "file":
        link = str(data.get("link", "")).strip()
        if not link:
            return []
        name = str(data.get("name") or content_id or "gofile.bin")
        return [
            ParsedSource(
                site=SourceSite.GOFILE.value,
                page_url=link,
                download_url=link,
                file_name=safe_name(name, fallback="gofile.bin"),
                remote_folder=None,
                metadata={"resource_id": content_id, "token": token},
            )
        ]

    items: list[ParsedSource] = []
    children = data.get("children") or {}
    for child_id, child in children.items():
        child_type = str(child.get("type", "")).lower()
        if child_type == "folder" and child.get("canAccess", True):
            nested_id = str(child.get("id") or child_id)
            items.extend(_gofile_list_sources(nested_id, token=token, password=password))
            continue
        if child_type != "file":
            continue
        link = str(child.get("link", "")).strip()
        if not link:
            continue
        name = str(child.get("name") or child_id or "gofile.bin")
        items.append(
            ParsedSource(
                site=SourceSite.GOFILE.value,
                page_url=link,
                download_url=link,
                file_name=safe_name(name, fallback="gofile.bin"),
                remote_folder=folder_name,
                metadata={"resource_id": str(child_id), "token": token},
            )
        )
    return items
