from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx

from ..user_agents import with_random_user_agent

_SITE_FILE_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".pdf",
    ".txt",
    ".csv",
}


def host(url: str) -> str:
    return urlparse(url).netloc.lower()


def segments(url: str) -> list[str]:
    return [x for x in urlparse(url).path.split("/") if x]


def safe_name(name: str, fallback: str = "file.bin") -> str:
    val = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return val or fallback


def guess_filename_from_path(url: str, fallback: str = "file.bin") -> str:
    path = urlparse(url).path
    candidate = path.split("/")[-1] if path else ""
    return safe_name(candidate, fallback=fallback) if candidate else fallback


def extract_drive_id(url: str) -> str | None:
    patterns = [
        re.compile(r"/file/d/([0-9A-Za-z_-]{10,})(?:/|$)", re.IGNORECASE),
        re.compile(r"/folders/([0-9A-Za-z_-]{10,})(?:/|$)", re.IGNORECASE),
        re.compile(r"id=([0-9A-Za-z_-]{10,})(?:&|$)", re.IGNORECASE),
    ]
    for pattern in patterns:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


def http_json(url: str, headers: dict[str, str], method: str = "GET", body: dict | None = None) -> dict:
    response = httpx.request(
        method=method,
        url=url,
        headers=with_random_user_agent(headers),
        json=body,
        timeout=20.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.text
    return json.loads(payload) if payload else {}


def http_text(url: str, headers: dict[str, str] | None = None, method: str = "GET") -> str:
    response = httpx.request(
        method=method,
        url=url,
        headers=with_random_user_agent(headers),
        timeout=20.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def is_direct_file_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _SITE_FILE_EXTENSIONS)
