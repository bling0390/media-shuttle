from __future__ import annotations

import re
from urllib.parse import urlparse

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
            site="GOFILE",
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"gofile_{resource_id}.bin"),
            remote_folder=remote_folder,
            metadata={"resource_id": resource_id},
        )
    ]


def parse_bunkr(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site="BUNKR",
            page_url=url,
            download_url=url,
            file_name=_safe_name(f"bunkr_{slug}.mp4"),
            remote_folder=slug,
            metadata={"slug": slug},
        )
    ]


def parse_mediafire(url: str) -> list[ParsedSource]:
    segs = _segments(url)
    slug = segs[-1] if segs else "unknown"
    return [
        ParsedSource(
            site="MEDIAFIRE",
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
            site="PIXELDRAIN",
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
            site="GD",
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
            site="MEGA",
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
            site="CYBERDROP",
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
            site="CYBERFILE",
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
            site="SAINT",
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
            site="COOMER",
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
            site="YTDL",
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
            site="GENERIC",
            page_url=url,
            download_url=url,
            file_name=name,
            remote_folder=urlparse(url).netloc or None,
        )
    ]


# live-only parsers

def parse_bunkr_album_live(url: str) -> list[ParsedSource]:
    try:
        from urllib.request import Request, urlopen

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
                site="BUNKR",
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
        from urllib.request import Request, urlopen

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
                site="CYBERDROP",
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
                ParseProvider("bunkr_album_live", "live", is_bunkr_album, parse_bunkr_album_live),
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
