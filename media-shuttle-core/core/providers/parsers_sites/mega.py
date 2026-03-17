from __future__ import annotations

import json
from base64 import urlsafe_b64decode
from urllib.parse import urlparse

import httpx
from Crypto.Cipher import AES

from ...enums import SourceSite
from ...models import ParsedSource
from .common import host, safe_name, segments

_MEGA_API_URL = "https://g.api.mega.co.nz/cs?id=0"
_ZERO_IV = b"\0" * 16


def is_mega(url: str) -> bool:
    return "mega.nz" in host(url)


def parse_mega(url: str) -> list[ParsedSource]:
    source = _mega_source(page_url=url, download_url=url)
    return [source] if source else []


def parse_mega_live(url: str) -> list[ParsedSource]:
    return parse_mega(url)


def resolve_mega_source(url: str) -> ParsedSource | None:
    resource_id = _mega_extract_id(url)
    if not resource_id:
        return None

    try:
        payload = _mega_request_public_download(resource_id)
    except Exception:
        return None

    direct_url = str(payload.get("g") or "").strip()
    if not direct_url:
        return None

    source = _mega_source(page_url=url, download_url=direct_url, payload=payload)
    if not source:
        return None
    source.metadata["resolved_live"] = True
    return source


def _mega_source(page_url: str, download_url: str, payload: dict | None = None) -> ParsedSource | None:
    resource_id = _mega_extract_id(page_url) or _mega_extract_id(download_url)
    if not resource_id:
        return None

    resolved_name = _mega_filename(page_url, payload=payload)
    file_name = safe_name(resolved_name, fallback=f"mega_{resource_id}.bin")
    return ParsedSource(
        site=SourceSite.MEGA.value,
        page_url=page_url,
        download_url=download_url,
        file_name=file_name,
        remote_folder=resource_id,
        metadata={"resource_id": resource_id, "resolved_live": download_url != page_url},
    )


def _mega_filename(url: str, payload: dict | None = None) -> str:
    link_key = _mega_extract_key(url)
    attr_blob = str((payload or {}).get("at") or "").strip()
    if not link_key or not attr_blob:
        return ""

    key_words = _mega_key_words(link_key)
    if len(key_words) < 8:
        return ""

    file_key = _mega_file_key(key_words)
    attrs = _mega_decrypt_attrs(file_key, attr_blob)
    return str(attrs.get("n") or "").strip()


def _mega_extract_id(url: str) -> str:
    segs = segments(url)
    if len(segs) >= 2 and segs[0].lower() in {"file", "folder"}:
        return segs[1]
    if segs:
        return segs[-1]

    fragment = urlparse(url).fragment.strip()
    if "!" in fragment:
        parts = [part for part in fragment.split("!") if part]
        if parts:
            return parts[0]
    return ""


def _mega_extract_key(url: str) -> str:
    parsed = urlparse(url)
    fragment = parsed.fragment.strip()
    if not fragment:
        return ""

    segs = segments(url)
    if len(segs) >= 2 and segs[0].lower() in {"file", "folder"}:
        return fragment.split("?")[0].strip()

    if "!" in fragment:
        parts = [part for part in fragment.split("!") if part]
        if len(parts) >= 2:
            return parts[-1].strip()
    return fragment.split("?")[0].strip()


def _mega_request_public_download(resource_id: str) -> dict:
    response = httpx.request(
        method="POST",
        url=_MEGA_API_URL,
        json=[{"a": "g", "p": resource_id, "g": 1}],
        timeout=20.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or not payload:
        return {}
    result = payload[0]
    return result if isinstance(result, dict) else {}


def _mega_key_words(link_key: str) -> list[int]:
    data = _mega_base64_url_decode(link_key)
    if len(data) % 4 != 0:
        return []
    return [int.from_bytes(data[index : index + 4], "big") for index in range(0, len(data), 4)]


def _mega_file_key(key_words: list[int]) -> bytes:
    if len(key_words) >= 8:
        words = [
            key_words[0] ^ key_words[4],
            key_words[1] ^ key_words[5],
            key_words[2] ^ key_words[6],
            key_words[3] ^ key_words[7],
        ]
    else:
        words = key_words[:4]
    return b"".join((word & 0xFFFFFFFF).to_bytes(4, "big") for word in words[:4])


def _mega_decrypt_attrs(file_key: bytes, attr_blob: str) -> dict:
    if len(file_key) != 16:
        return {}

    encrypted = _mega_base64_url_decode(attr_blob)
    if not encrypted:
        return {}

    plain = AES.new(file_key, AES.MODE_CBC, iv=_ZERO_IV).decrypt(encrypted).rstrip(b"\0")
    if not plain.startswith(b"MEGA"):
        return {}

    try:
        return json.loads(plain[4:].decode("utf-8"))
    except Exception:
        return {}


def _mega_base64_url_decode(value: str) -> bytes:
    normalized = value.replace("-", "+").replace("_", "/")
    padding = "=" * ((4 - len(normalized) % 4) % 4)
    return urlsafe_b64decode((normalized + padding).encode("ascii"))
