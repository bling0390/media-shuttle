from __future__ import annotations

import hashlib
import os
import time
from urllib.parse import urlencode, urlparse

from ...enums import SourceSite
from ...models import ParsedSource
from ..user_agents import with_random_user_agent
from .common import host, http_json, safe_name, segments

_GOFILE_TOKEN: str = ""
_GOFILE_TOKEN_EXPIRES_AT: int = 0
_GOFILE_WT_SALT = "gf2026x"
_GOFILE_DEFAULT_LANGUAGE = os.getenv("MEDIA_SHUTTLE_GOFILE_LANGUAGE", "en-US").strip() or "en-US"


def is_gofile(url: str) -> bool:
    return "gofile.io" in host(url)


def parse_gofile(url: str) -> list[ParsedSource]:
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
    global _GOFILE_TOKEN, _GOFILE_TOKEN_EXPIRES_AT

    static_token = os.getenv("MEDIA_SHUTTLE_GOFILE_TOKEN", "").strip()
    if static_token:
        return static_token

    now = int(time.time())
    if _GOFILE_TOKEN and now < _GOFILE_TOKEN_EXPIRES_AT:
        return _GOFILE_TOKEN

    resp = http_json(
        "https://api.gofile.io/accounts",
        headers=with_random_user_agent(
            {
                "Accept": "*/*",
                "Connection": "keep-alive",
            }
        ),
        method="POST",
    )
    token = str(resp.get("data", {}).get("token", "")).strip() if resp.get("status") == "ok" else ""
    if not token:
        raise RuntimeError("failed to get gofile token")

    _GOFILE_TOKEN = token
    _GOFILE_TOKEN_EXPIRES_AT = now + 60 * 60
    return token


def _gofile_list_sources(content_id: str, token: str, password: str | None = None) -> list[ParsedSource]:
    request_headers = with_random_user_agent(
        {
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Authorization": f"Bearer {token}",
        }
    )
    user_agent = request_headers["User-Agent"]
    language = _GOFILE_DEFAULT_LANGUAGE
    request_headers["X-Website-Token"] = _gofile_build_website_token(token, user_agent, language)
    request_headers["X-BL"] = language

    params = {"cache": "true"}
    if password:
        params["password"] = password

    resp = http_json(
        f"https://api.gofile.io/contents/{content_id}?{urlencode(params)}",
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


def _gofile_build_website_token(token: str, user_agent: str, language: str, now: int | None = None) -> str:
    unix_time = int(time.time() if now is None else now)
    time_bucket = unix_time // (4 * 60 * 60)
    payload = f"{user_agent}::{language}::{token}::{time_bucket}::{_GOFILE_WT_SALT}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
