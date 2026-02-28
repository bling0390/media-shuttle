from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from ..enums import SourceSite
from ..models import ParsedSource
from .types import ParseProvider

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

_GOFILE_TOKEN: str = ""
_GOFILE_TOKEN_EXPIRES_AT: int = 0


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _segments(url: str) -> list[str]:
    return [x for x in urlparse(url).path.split("/") if x]


def _safe_name(name: str, fallback: str = "file.bin") -> str:
    val = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return val or fallback


def _guess_filename_from_path(url: str, fallback: str = "file.bin") -> str:
    path = urlparse(url).path
    candidate = path.split("/")[-1] if path else ""
    return _safe_name(candidate, fallback=fallback) if candidate else fallback


def _extract_drive_id(url: str) -> str | None:
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


def _http_json(url: str, headers: dict[str, str], method: str = "GET") -> dict:
    req = Request(url, headers=headers, method=method)
    with urlopen(req, timeout=20) as resp:
        payload = resp.read().decode("utf-8", errors="ignore")
    return json.loads(payload) if payload else {}


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

    resp = _http_json(
        "https://api.gofile.io/accounts",
        headers={
            "User-Agent": "media-shuttle-core",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "*/*",
            "Connection": "keep-alive",
        },
        method="POST",
    )
    token = str(resp.get("data", {}).get("token", "")).strip() if resp.get("status") == "ok" else ""
    if not token:
        raise RuntimeError("failed to get gofile token")

    _GOFILE_TOKEN = token
    _GOFILE_TOKEN_EXPIRES_AT = now + 60 * 60
    return token


def _gofile_list_sources(content_id: str, token: str, password: str | None = None) -> list[ParsedSource]:
    params = {"wt": "4fd6sg89d7s6", "cache": "true"}
    if password:
        params["password"] = password

    resp = _http_json(
        f"https://api.gofile.io/contents/{content_id}?{urlencode(params)}",
        headers={
            "User-Agent": "media-shuttle-core",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Authorization": f"Bearer {token}",
        },
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
                file_name=_safe_name(name, fallback="gofile.bin"),
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
                file_name=_safe_name(name, fallback="gofile.bin"),
                remote_folder=folder_name,
                metadata={"resource_id": str(child_id), "token": token},
            )
        )
    return items


# matchers

def is_gofile(url: str) -> bool:
    return "gofile.io" in _host(url)


def is_bunkr(url: str) -> bool:
    return "bunkr" in _host(url)


def is_bunkr_album(url: str) -> bool:
    return is_bunkr(url) and "/a/" in urlparse(url).path.lower()


def is_mediafire(url: str) -> bool:
    return "mediafire.com" in _host(url)


def is_pixeldrain(url: str) -> bool:
    return "pixeldrain.com" in _host(url)


def is_gd(url: str) -> bool:
    return "drive.google.com" in _host(url)


def is_mega(url: str) -> bool:
    return "mega.nz" in _host(url)


def is_cyberdrop(url: str) -> bool:
    return "cyberdrop" in _host(url)


def is_cyberdrop_album(url: str) -> bool:
    return is_cyberdrop(url) and "/a/" in urlparse(url).path.lower()


def is_cyberfile(url: str) -> bool:
    return "cyberfile.me" in _host(url)


def is_saint(url: str) -> bool:
    return "saint.to" in _host(url)


def is_coomer(url: str) -> bool:
    host = _host(url)
    return "coomer" in host or "kemono" in host


def is_ytdl(url: str) -> bool:
    host = _host(url)
    return any(site in host for site in ["youtube.com", "youtu.be", "vimeo.com", "dailymotion.com"])


def is_direct_file(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _SITE_FILE_EXTENSIONS)


# parser funcs

def parse_gofile(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    resource_id = segs[-1] if segs else "unknown"
    remote_folder = resource_id if "/d/" in urlparse(url).path else None
    return [
        ParsedSource(
            site=SourceSite.GOFILE.value,
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"gofile_{resource_id}.bin"),
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
    return _gofile_list_sources(content_id, token=token) or parse_gofile(url)


def parse_bunkr(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site=SourceSite.BUNKR.value,
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"bunkr_{slug}.mp4"),
            remote_folder=slug,
            metadata={"slug": slug},
        )
    ]


def parse_bunkr_live(url: str) -> list[ParsedSource]:
    # Keep page URL here; downloader resolves direct media URL when needed.
    return parse_bunkr(url)


def parse_mediafire(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site=SourceSite.MEDIAFIRE.value,
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"mediafire_{slug}.bin"),
            remote_folder=slug,
        )
    ]


def parse_pixeldrain(url: str) -> list[ParsedSource]:
    segs = _segments(url)
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
            file_name=_safe_name(f"pixeldrain_{slug}.bin"),
            remote_folder=slug,
        )
    ]


def parse_gd(url: str) -> list[ParsedSource]:
    file_id = _extract_drive_id(url)
    if not file_id:
        return []
    return [
        ParsedSource(
            site=SourceSite.GD.value,
            page_url=url,
            download_url=f"https://drive.usercontent.google.com/download?id={file_id}&export=download&authuser=0",
            file_name=_safe_name(f"gd_{file_id}.bin"),
            remote_folder=file_id,
            metadata={"file_id": file_id},
        )
    ]


def parse_mega(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    slug = segs[-1] if segs else "file"
    return [
        ParsedSource(
            site=SourceSite.MEGA.value,
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"mega_{slug}.bin"),
            remote_folder=slug,
        )
    ]


def parse_cyberdrop(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site=SourceSite.CYBERDROP.value,
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"cyberdrop_{slug}.bin"),
            remote_folder=slug,
        )
    ]


def parse_cyberfile(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site=SourceSite.CYBERFILE.value,
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"cyberfile_{slug}.bin"),
            remote_folder=slug,
        )
    ]


def parse_saint(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site=SourceSite.SAINT.value,
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"saint_{slug}.bin"),
            remote_folder=slug,
        )
    ]


def parse_coomer(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site=SourceSite.COOMER.value,
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"coomer_{slug}.bin"),
            remote_folder=slug,
        )
    ]


def parse_ytdl(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    slug = segs[-1] if segs else "video"
    return [
        ParsedSource(
            site=SourceSite.YTDL.value,
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"ytdl_{slug}.mp4"),
            remote_folder=slug,
        )
    ]


def parse_generic(url: str) -> list[ParsedSource]:
    name = _guess_filename_from_path(url, fallback="download.bin")
    return [
        ParsedSource(
            site=SourceSite.GENERIC.value,
            page_url=url,
            download_url=url,
            file_name=name,
            remote_folder=urlparse(url).netloc or None,
        )
    ]


# live-only parsers

def parse_bunkr_album_live(url: str) -> list[ParsedSource]:
    try:
        parsed = urlparse(url)
        album = _segments(url)[-1] if _segments(url) else "album"
        req = Request(url, headers={"User-Agent": "media-shuttle-core"})
        with urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        links = re.findall(r'href=["\'](/(?:f|v)/[^"\']+)["\']', html)
        if not links:
            return []
        return [
            ParsedSource(
                site=SourceSite.BUNKR.value,
                page_url=f"{parsed.scheme}://{parsed.netloc}{link}",
                download_url=f"{parsed.scheme}://{parsed.netloc}{link}",
                file_name=_safe_name(f"bunkr_{link.split('/')[-1]}.mp4"),
                remote_folder=album,
            )
            for link in links
        ]
    except Exception:
        return []


def parse_cyberdrop_album_live(url: str) -> list[ParsedSource]:
    try:
        parsed = urlparse(url)
        album = _segments(url)[-1] if _segments(url) else "album"
        req = Request(url, headers={"User-Agent": "media-shuttle-core"})
        with urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        links = re.findall(r'href=["\'](/f/[^"\']+)["\']', html)
        if not links:
            return []
        return [
            ParsedSource(
                site=SourceSite.CYBERDROP.value,
                page_url=f"{parsed.scheme}://{parsed.netloc}{link}",
                download_url=f"{parsed.scheme}://{parsed.netloc}{link}",
                file_name=_safe_name(f"cyberdrop_{link.split('/')[-1]}.bin"),
                remote_folder=album,
            )
            for link in links
        ]
    except Exception:
        return []


def builtin_parse_providers(mode: str) -> list[ParseProvider]:
    providers: list[ParseProvider] = []

    if mode == "live":
        providers.extend(
            [
                ParseProvider("gofile_live", "live", is_gofile, parse_gofile_live),
                ParseProvider("bunkr_album_live", "live", is_bunkr_album, parse_bunkr_album_live),
                ParseProvider("bunkr_live", "live", is_bunkr, parse_bunkr_live),
                ParseProvider("cyberdrop_album_live", "live", is_cyberdrop_album, parse_cyberdrop_album_live),
            ]
        )

    providers.extend(
        [
            ParseProvider("gofile", "mock", is_gofile, parse_gofile),
            ParseProvider("bunkr", "mock", is_bunkr, parse_bunkr),
            ParseProvider("cyberdrop", "mock", is_cyberdrop, parse_cyberdrop),
            ParseProvider("cyberfile", "mock", is_cyberfile, parse_cyberfile),
            ParseProvider("pixeldrain", "mock", is_pixeldrain, parse_pixeldrain),
            ParseProvider("gd", "mock", is_gd, parse_gd),
            ParseProvider("mega", "mock", is_mega, parse_mega),
            ParseProvider("saint", "mock", is_saint, parse_saint),
            ParseProvider("coomer", "mock", is_coomer, parse_coomer),
            ParseProvider("mediafire", "mock", is_mediafire, parse_mediafire),
            ParseProvider("ytdl", "mock", is_ytdl, parse_ytdl),
            ParseProvider("direct_file", "mock", is_direct_file, parse_generic),
            ParseProvider("generic_fallback", "all", lambda _url: True, parse_generic),
        ]
    )
    return providers
