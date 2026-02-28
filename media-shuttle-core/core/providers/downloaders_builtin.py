from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..enums import SourceSite
from ..models import DownloadResult, ParsedSource
from .types import DownloadProvider


def _download_dir() -> Path:
    path = Path(os.getenv("MEDIA_SHUTTLE_DOWNLOAD_DIR", "/tmp/media-shuttle"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _materialize_path(source: ParsedSource) -> Path:
    # Use temporary local name to avoid extremely long original filenames.
    seed = hashlib.sha1(source.download_url.encode("utf-8")).hexdigest()[:16]
    task_dir = _download_dir() / seed
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir / "tmp.part"


def _write_mock_file(path: Path, source: ParsedSource) -> int:
    payload = f"site={source.site};url={source.download_url}\n".encode("utf-8")
    path.write_bytes(payload)
    return len(payload)


def _http_download(url: str, path: Path, headers: dict[str, str] | None = None) -> int:
    req = Request(url, headers=headers or {"User-Agent": "media-shuttle-core"})
    with urlopen(req, timeout=60) as resp:
        data = resp.read()
    path.write_bytes(data)
    return len(data)


def _result(source: ParsedSource, output: Path, size: int, source_url: str | None = None) -> DownloadResult:
    return DownloadResult(
        site=source.site,
        source_url=source_url or source.download_url,
        local_path=str(output),
        size_bytes=size,
        file_name=source.file_name,
        remote_folder=source.remote_folder,
    )


def download_mock(source: ParsedSource) -> DownloadResult:
    output = _materialize_path(source)
    return _result(source, output, _write_mock_file(output, source))


def download_live_generic(
    source: ParsedSource,
    headers: dict[str, str] | None = None,
    actual_url: str | None = None,
) -> DownloadResult:
    url = actual_url or source.download_url
    output = _materialize_path(source)
    return _result(source, output, _http_download(url, output, headers=headers), source_url=url)


def download_gofile_live(source: ParsedSource) -> DownloadResult:
    token = source.metadata.get("token")
    headers = {"User-Agent": "media-shuttle-core"}
    if token:
        headers["Cookie"] = f"accountToken={token}"
    return download_live_generic(source, headers=headers)


def _is_direct_file_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return bool(re.search(r"\.(mp4|mov|mkv|avi|webm|jpg|jpeg|png|gif|zip|rar|7z|tar|gz|pdf|mp3|m4a)(?:$|/)", path))


def _resolve_bunkr_actual_url(source: ParsedSource) -> str:
    parsed = urlparse(source.download_url)
    path = parsed.path.lower()
    if _is_direct_file_url(source.download_url):
        return source.download_url
    if "/f/" not in path and "/v/" not in path:
        return source.download_url

    req = Request(source.download_url, headers={"User-Agent": "media-shuttle-core", "Referer": source.page_url})
    with urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    # Bunkr pages may embed direct media links in src/href/json fields.
    candidates = re.findall(r"https?://[^\s\"'<>]+", html)
    for raw in candidates:
        candidate = raw.replace("\\/", "/")
        if _is_direct_file_url(candidate):
            return candidate
    return source.download_url


def download_bunkr_live(source: ParsedSource) -> DownloadResult:
    headers = {
        "User-Agent": "media-shuttle-core",
        "Referer": source.page_url,
        "Range": "bytes=0-",
    }
    actual_url = _resolve_bunkr_actual_url(source)
    return download_live_generic(source, headers=headers, actual_url=actual_url)


def download_cyberdrop_live(source: ParsedSource) -> DownloadResult:
    headers = {"User-Agent": "media-shuttle-core", "Referer": source.page_url}
    return download_live_generic(source, headers=headers)


def download_cyberfile_live(source: ParsedSource) -> DownloadResult:
    headers = {"User-Agent": "media-shuttle-core", "Referer": source.page_url}
    return download_live_generic(source, headers=headers)


def download_mediafire_live(source: ParsedSource) -> DownloadResult:
    return download_live_generic(source, headers={"User-Agent": "media-shuttle-core"})


def download_pixeldrain_live(source: ParsedSource) -> DownloadResult:
    return download_live_generic(source, headers={"User-Agent": "media-shuttle-core"})


def download_gd_live(source: ParsedSource) -> DownloadResult:
    return download_live_generic(source, headers={"User-Agent": "media-shuttle-core"})


def download_saint_live(source: ParsedSource) -> DownloadResult:
    return download_live_generic(source, headers={"User-Agent": "media-shuttle-core"})


def download_coomer_live(source: ParsedSource) -> DownloadResult:
    return download_live_generic(source, headers={"User-Agent": "media-shuttle-core"})


def download_unsupported_live(source: ParsedSource) -> DownloadResult:
    raise RuntimeError(f"live downloader is not implemented for site: {source.site}")


def builtin_download_providers(mode: str) -> list[DownloadProvider]:
    providers: list[DownloadProvider] = []

    if mode == "live":
        providers.extend(
            [
                DownloadProvider(
                    "gofile_live", "live", lambda source: source.site == SourceSite.GOFILE.value, download_gofile_live
                ),
                DownloadProvider(
                    "bunkr_live", "live", lambda source: source.site == SourceSite.BUNKR.value, download_bunkr_live
                ),
                DownloadProvider(
                    "cyberdrop_live",
                    "live",
                    lambda source: source.site == SourceSite.CYBERDROP.value,
                    download_cyberdrop_live,
                ),
                DownloadProvider(
                    "cyberfile_live",
                    "live",
                    lambda source: source.site == SourceSite.CYBERFILE.value,
                    download_cyberfile_live,
                ),
                DownloadProvider(
                    "pixeldrain_live",
                    "live",
                    lambda source: source.site == SourceSite.PIXELDRAIN.value,
                    download_pixeldrain_live,
                ),
                DownloadProvider("gd_live", "live", lambda source: source.site == SourceSite.GD.value, download_gd_live),
                DownloadProvider(
                    "mediafire_live",
                    "live",
                    lambda source: source.site == SourceSite.MEDIAFIRE.value,
                    download_mediafire_live,
                ),
                DownloadProvider(
                    "saint_live", "live", lambda source: source.site == SourceSite.SAINT.value, download_saint_live
                ),
                DownloadProvider(
                    "coomer_live", "live", lambda source: source.site == SourceSite.COOMER.value, download_coomer_live
                ),
                DownloadProvider("mega_live", "live", lambda source: source.site == SourceSite.MEGA.value, download_unsupported_live),
                DownloadProvider("ytdl_live", "live", lambda source: source.site == SourceSite.YTDL.value, download_unsupported_live),
            ]
        )

    providers.extend(
        [
            DownloadProvider("gofile_mock", "mock", lambda source: source.site == SourceSite.GOFILE.value, download_mock),
            DownloadProvider("bunkr_mock", "mock", lambda source: source.site == SourceSite.BUNKR.value, download_mock),
            DownloadProvider(
                "cyberdrop_mock", "mock", lambda source: source.site == SourceSite.CYBERDROP.value, download_mock
            ),
            DownloadProvider(
                "cyberfile_mock", "mock", lambda source: source.site == SourceSite.CYBERFILE.value, download_mock
            ),
            DownloadProvider(
                "pixeldrain_mock", "mock", lambda source: source.site == SourceSite.PIXELDRAIN.value, download_mock
            ),
            DownloadProvider("gd_mock", "mock", lambda source: source.site == SourceSite.GD.value, download_mock),
            DownloadProvider("mega_mock", "mock", lambda source: source.site == SourceSite.MEGA.value, download_mock),
            DownloadProvider("saint_mock", "mock", lambda source: source.site == SourceSite.SAINT.value, download_mock),
            DownloadProvider("coomer_mock", "mock", lambda source: source.site == SourceSite.COOMER.value, download_mock),
            DownloadProvider(
                "mediafire_mock", "mock", lambda source: source.site == SourceSite.MEDIAFIRE.value, download_mock
            ),
            DownloadProvider("ytdl_mock", "mock", lambda source: source.site == SourceSite.YTDL.value, download_mock),
            DownloadProvider("generic_mock", "all", lambda _source: True, download_mock),
        ]
    )
    return providers
