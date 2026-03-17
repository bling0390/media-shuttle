from __future__ import annotations

import os
from base64 import urlsafe_b64decode
from urllib.parse import urlencode

import httpx

from ...enums import SourceSite
from ...models import ParsedSource
from .common import host, safe_name, segments
from .mega import _mega_decrypt_attrs, _mega_file_key, _mega_key_words

_TRANSFER_API_URL = "https://g.api.mega.co.nz/cs"
_TRANSFER_DEFAULT_LANG = os.getenv("MEDIA_SHUTTLE_TRANSFERIT_LANGUAGE", "en").strip() or "en"


def is_transfer(url: str) -> bool:
    hostname = host(url)
    return hostname == "transfer.it" or hostname.endswith(".transfer.it")


def parse_transfer(url: str) -> list[ParsedSource]:
    share_id = _transfer_extract_id(url)
    if not share_id:
        return []
    return [
        ParsedSource(
            site=SourceSite.TRANSFERIT.value,
            page_url=url,
            download_url=url,
            file_name=safe_name(f"transfer_{share_id}.bin"),
            remote_folder=share_id,
            metadata={"share_id": share_id, "resolved_live": False},
        )
    ]


def parse_transfer_live(url: str) -> list[ParsedSource]:
    share_id = _transfer_extract_id(url)
    if not share_id:
        return []

    try:
        nodes = _transfer_list_nodes(share_id)
    except Exception:
        return parse_transfer(url)
    try:
        share_title = _transfer_share_title(share_id)
    except Exception:
        share_title = ""

    sources = _transfer_sources_from_nodes(url, share_id, nodes, share_title=share_title)
    return sources or parse_transfer(url)


def resolve_transfer_source(source: ParsedSource | str) -> ParsedSource | None:
    if isinstance(source, ParsedSource):
        page_url = source.page_url
        current = source
    else:
        page_url = source
        current = None

    share_id = (current.metadata.get("share_id") if current else "") or _transfer_extract_id(page_url)
    if not share_id:
        return None

    share_title = str(current.metadata.get("share_title") or "").strip() if current else ""
    if not share_title:
        try:
            share_title = _transfer_share_title(share_id)
        except Exception:
            share_title = ""

    try:
        nodes = _transfer_list_nodes(share_id)
    except Exception:
        return None

    file_nodes = [node for node in nodes if int(node.get("t") or 0) == 0]
    if not file_nodes:
        return None

    selected = _transfer_select_node(file_nodes, current)
    if not selected:
        return None

    try:
        payload = _transfer_public_download(share_id, str(selected.get("h") or "").strip())
    except Exception:
        return None

    direct_url = str(payload.get("g") or "").strip()
    if not direct_url:
        return None

    resolved_name = _transfer_node_name(selected)
    file_name = safe_name(
        resolved_name or (current.file_name if current else "") or f"transfer_{share_id}.bin",
        fallback=f"transfer_{share_id}.bin",
    )
    remote_folder = _transfer_remote_folder(share_id, share_title, len(file_nodes))

    metadata = dict(current.metadata) if current else {}
    metadata.update(
        {
            "share_id": share_id,
            "node_handle": str(selected.get("h") or "").strip(),
            "share_title": share_title,
            "resolved_live": True,
        }
    )

    return ParsedSource(
        site=SourceSite.TRANSFERIT.value,
        page_url=page_url,
        download_url=direct_url,
        file_name=file_name,
        remote_folder=remote_folder,
        metadata=metadata,
    )


def _transfer_sources_from_nodes(
    page_url: str,
    share_id: str,
    nodes: list[dict],
    share_title: str = "",
) -> list[ParsedSource]:
    file_nodes = [node for node in nodes if int(node.get("t") or 0) == 0]
    if not file_nodes:
        return []

    remote_folder = _transfer_remote_folder(share_id, share_title, len(file_nodes))
    sources: list[ParsedSource] = []
    for node in file_nodes:
        node_handle = str(node.get("h") or "").strip()
        if not node_handle:
            continue
        file_name = safe_name(_transfer_node_name(node), fallback=f"transfer_{node_handle}.bin")
        sources.append(
            ParsedSource(
                site=SourceSite.TRANSFERIT.value,
                page_url=page_url,
                download_url=page_url,
                file_name=file_name,
                remote_folder=remote_folder,
                metadata={
                    "share_id": share_id,
                    "node_handle": node_handle,
                    "share_title": share_title,
                    "resolved_live": False,
                },
            )
        )
    return sources


def _transfer_select_node(file_nodes: list[dict], source: ParsedSource | None) -> dict | None:
    if not file_nodes:
        return None

    if source:
        desired_handle = str(source.metadata.get("node_handle") or "").strip()
        if desired_handle:
            for node in file_nodes:
                if str(node.get("h") or "").strip() == desired_handle:
                    return node

        desired_name = source.file_name.strip()
        if desired_name:
            for node in file_nodes:
                if _transfer_node_name(node) == desired_name:
                    return node

    return file_nodes[0]


def _transfer_remote_folder(share_id: str, share_title: str, file_count: int) -> str:
    if file_count > 1 and share_title:
        return share_title
    return share_id


def _transfer_extract_id(url: str) -> str:
    segs = segments(url)
    if len(segs) >= 2 and segs[0].lower() == "t":
        return segs[1]
    return segs[-1] if segs else ""


def _transfer_share_title(share_id: str) -> str:
    payload = _transfer_request(share_id, [{"a": "xi", "xh": share_id}], include_share_param=True)
    if not payload or not isinstance(payload[0], dict):
        return ""
    raw_title = str(payload[0].get("t") or "").strip()
    if not raw_title:
        return ""
    return _transfer_base64url_text(raw_title)


def _transfer_list_nodes(share_id: str) -> list[dict]:
    payload = _transfer_request(share_id, [{"a": "f", "c": 1, "r": 1, "xnc": 1}], include_share_param=True)
    if not payload or not isinstance(payload[0], dict):
        return []
    nodes = payload[0].get("f")
    return [node for node in nodes if isinstance(node, dict)] if isinstance(nodes, list) else []


def _transfer_public_download(share_id: str, node_handle: str) -> dict:
    payload = _transfer_request(
        share_id,
        [{"a": "g", "n": node_handle, "pt": 1, "g": 1, "ssl": 1}],
        include_share_param=True,
    )
    if not payload or not isinstance(payload[0], dict):
        return {}
    return payload[0]


def _transfer_request(share_id: str, body: list[dict], include_share_param: bool) -> list:
    query = {
        "id": "0",
        "v": "3",
        "lang": _TRANSFER_DEFAULT_LANG,
        "domain": "transferit",
        "bc": "1",
    }
    if include_share_param:
        query["x"] = share_id

    response = httpx.request(
        method="POST",
        url=f"{_TRANSFER_API_URL}?{urlencode(query)}",
        json=body,
        timeout=20.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def _transfer_node_name(node: dict) -> str:
    attr_blob = str(node.get("a") or "").strip()
    node_key = str(node.get("k") or "").strip()
    if not attr_blob or not node_key:
        return ""

    key_words = _mega_key_words(node_key)
    if len(key_words) < 8:
        return ""

    attrs = _mega_decrypt_attrs(_mega_file_key(key_words), attr_blob)
    return str(attrs.get("n") or "").strip()


def _transfer_base64url_text(value: str) -> str:
    normalized = value.replace("-", "+").replace("_", "/")
    padding = "=" * ((4 - len(normalized) % 4) % 4)
    try:
        return urlsafe_b64decode((normalized + padding).encode("ascii")).decode("utf-8").strip()
    except Exception:
        return ""
