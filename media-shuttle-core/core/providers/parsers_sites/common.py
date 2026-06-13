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


def http_json(
    url: str,
    headers: dict[str, str],
    method: str = "GET",
    body: dict | None = None,
) -> dict:
    """Fetch a JSON resource.

    The gofile v2 API in particular returns
    ``{"status": "error-notPremium", "data": {}}`` with
    ``HTTP 401`` for content that a guest token is not
    allowed to read; the structured ``status`` field is
    what callers should branch on, not the HTTP status
    code. We therefore do **not** call
    :py:meth:`httpx.Response.raise_for_status` and
    instead return the decoded body unconditionally so
    callers can inspect ``status`` themselves.

    If the body is not JSON (e.g. an upstream nginx
    502 with an HTML body) we return ``{}`` so the
    caller's ``payload.get(\"status\") != \"ok\"`` check
    fires and produces a structured failure rather
    than a stack trace.
    """
    response = httpx.request(
        method=method,
        url=url,
        headers=with_random_user_agent(headers),
        json=body,
        timeout=20.0,
        follow_redirects=True,
    )
    payload = response.text
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {}


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
