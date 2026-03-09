from __future__ import annotations

import json
import re
from urllib.parse import quote, urlparse, urlunparse

from ...enums import SourceSite
from ...models import ParsedSource
from .common import host, http_json, http_text, safe_name, segments


def is_pixeldrain(url: str) -> bool:
    return "pixeldrain." in host(url)


def parse_pixeldrain(url: str) -> list[ParsedSource]:
    segs = segments(url)
    slug = segs[-1] if segs else "unknown"
    parsed = urlparse(url)
    path = parsed.path.lower()
    if "/u/" in path:
        download_url = f"{parsed.scheme}://{parsed.netloc}/api/file/{slug}"
    elif "/l/" in path:
        download_url = f"{parsed.scheme}://{parsed.netloc}/api/list/{slug}"
    else:
        download_url = url
    return [
        ParsedSource(
            site=SourceSite.PIXELDRAIN.value,
            page_url=url,
            download_url=download_url,
            file_name=safe_name(f"pixeldrain_{slug}.bin"),
            remote_folder=slug,
        )
    ]


def parse_pixeldrain_live(url: str) -> list[ParsedSource]:
    parsed = urlparse(url)
    path = parsed.path.lower()
    resource_id = _pixeldrain_extract_id(url)
    if not resource_id:
        return []

    if "/u/" in path:
        return [
            ParsedSource(
                site=SourceSite.PIXELDRAIN.value,
                page_url=url,
                download_url=_pixeldrain_api_url(url, f"/api/file/{resource_id}"),
                file_name=safe_name(f"pixeldrain_{resource_id}.bin"),
                remote_folder=None,
                metadata={"resource_id": resource_id},
            )
        ]

    if "/l/" in path:
        try:
            payload = http_json(_pixeldrain_api_url(url, f"/api/list/{resource_id}"), headers={})
        except Exception:
            return []
        return _pixeldrain_sources_from_list(url, resource_id, payload)

    if "/d/" in path:
        html = _pixeldrain_fetch_share_page(url)
        if not html:
            return []
        return _pixeldrain_sources_from_filesystem_page(url, resource_id, html)

    return []


def _pixeldrain_extract_id(url: str) -> str | None:
    segs = segments(url)
    if not segs:
        return None
    if len(segs) >= 2 and segs[0].lower() in {"u", "l", "d"}:
        return segs[1]
    return segs[-1]


def _pixeldrain_sources_from_list(url: str, resource_id: str, payload: dict) -> list[ParsedSource]:
    if not payload.get("success"):
        return []

    folder_name = str(payload.get("title") or resource_id or "pixeldrain").strip() or resource_id
    files = payload.get("files") or []
    items: list[ParsedSource] = []
    for file_info in files:
        file_id = str(file_info.get("id") or "").strip()
        if not file_id:
            continue
        file_name = str(file_info.get("name") or file_id or "pixeldrain.bin")
        items.append(
            ParsedSource(
                site=SourceSite.PIXELDRAIN.value,
                page_url=url,
                download_url=_pixeldrain_api_url(url, f"/api/file/{file_id}"),
                file_name=safe_name(file_name, fallback=f"pixeldrain_{file_id}.bin"),
                remote_folder=folder_name,
                metadata={"resource_id": file_id, "list_id": resource_id},
            )
        )
    return items


def _pixeldrain_fetch_share_page(url: str) -> str:
    candidates = [_pixeldrain_variant_url(url)]
    fallback = _pixeldrain_variant_url(url, preferred_netloc="pixeldrain.net")
    if fallback not in candidates:
        candidates.append(fallback)
    for candidate in candidates:
        try:
            return http_text(candidate, headers={})
        except Exception:
            continue
    return ""


def _pixeldrain_sources_from_filesystem_page(url: str, resource_id: str, html: str) -> list[ParsedSource]:
    node = _pixeldrain_extract_initial_node(html)
    if not node:
        return []

    path_nodes = node.get("path") or []
    folder_name = resource_id
    if path_nodes:
        folder_name = str(path_nodes[-1].get("name") or resource_id).strip() or resource_id

    items: list[ParsedSource] = []
    for child in node.get("children") or []:
        if str(child.get("type", "")).lower() != "file":
            continue
        name = str(child.get("name") or "").strip()
        full_path = str(child.get("path") or "").strip()
        if not name or name.startswith(".") or not full_path:
            continue
        relative_path = _pixeldrain_relative_filesystem_path(full_path, resource_id)
        if not relative_path:
            continue
        items.append(
            ParsedSource(
                site=SourceSite.PIXELDRAIN.value,
                page_url=url,
                download_url=_pixeldrain_api_url(url, f"/api/filesystem/{resource_id}/{quote(relative_path, safe='/')}"),
                file_name=safe_name(name, fallback=f"pixeldrain_{resource_id}.bin"),
                remote_folder=folder_name,
                metadata={"resource_id": resource_id, "path": relative_path},
            )
        )
    return items


def _pixeldrain_extract_initial_node(html: str) -> dict:
    matched = re.search(r"window\.initial_node = (\{.*?\});\s*window\.user =", html, flags=re.DOTALL)
    if not matched:
        return {}
    try:
        return json.loads(matched.group(1))
    except json.JSONDecodeError:
        return {}


def _pixeldrain_relative_filesystem_path(full_path: str, resource_id: str) -> str:
    prefix = f"/{resource_id}/"
    if full_path.startswith(prefix):
        return full_path[len(prefix) :]
    return full_path.lstrip("/")


def _pixeldrain_api_url(url: str, api_path: str) -> str:
    parsed = urlparse(_pixeldrain_variant_url(url))
    return urlunparse((parsed.scheme, parsed.netloc, api_path, "", "", ""))


def _pixeldrain_variant_url(url: str, preferred_netloc: str | None = None) -> str:
    parsed = urlparse(url)
    netloc = preferred_netloc or parsed.netloc or "pixeldrain.com"
    return urlunparse((parsed.scheme or "https", netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
