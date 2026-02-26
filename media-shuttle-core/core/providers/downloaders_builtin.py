from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.request import Request, urlopen

from ..models import DownloadResult, ParsedSource
from .types import DownloadProvider


def _download_dir() -> Path:
    path = Path(os.getenv("MEDIA_SHUTTLE_DOWNLOAD_DIR", "/tmp/media-shuttle"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name) or "download.bin"


def _materialize_path(source: ParsedSource) -> Path:
    seed = hashlib.sha1(source.download_url.encode("utf-8")).hexdigest()[:8]
    return _download_dir() / f"{seed}_{_safe_filename(source.file_name)}"


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


def _result(source: ParsedSource, output: Path, size: int) -> DownloadResult:
    return DownloadResult(
        site=source.site,
        source_url=source.download_url,
        local_path=str(output),
        size_bytes=size,
        file_name=source.file_name,
        remote_folder=source.remote_folder,
    )


def download_mock(source: ParsedSource) -> DownloadResult:
    output = _materialize_path(source)
    return _result(source, output, _write_mock_file(output, source))


def download_live_generic(source: ParsedSource, headers: dict[str, str] | None = None) -> DownloadResult:
    output = _materialize_path(source)
    return _result(source, output, _http_download(source.download_url, output, headers=headers))


def download_gofile_live(source: ParsedSource) -> DownloadResult:
    token = source.metadata.get("token")
    headers = {"User-Agent": "media-shuttle-core"}
    if token:
        headers["Cookie"] = f"accountToken={token}"
    return download_live_generic(source, headers=headers)


def download_bunkr_live(source: ParsedSource) -> DownloadResult:
    headers = {
        "User-Agent": "media-shuttle-core",
        "Referer": source.page_url,
        "Range": "bytes=0-",
    }
    return download_live_generic(source, headers=headers)


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
                DownloadProvider("gofile_live", "live", lambda source: source.site == "GOFILE", download_gofile_live),
                DownloadProvider("bunkr_live", "live", lambda source: source.site == "BUNKR", download_bunkr_live),
                DownloadProvider("cyberdrop_live", "live", lambda source: source.site == "CYBERDROP", download_cyberdrop_live),
                DownloadProvider("cyberfile_live", "live", lambda source: source.site == "CYBERFILE", download_cyberfile_live),
                DownloadProvider("pixeldrain_live", "live", lambda source: source.site == "PIXELDRAIN", download_pixeldrain_live),
                DownloadProvider("gd_live", "live", lambda source: source.site == "GD", download_gd_live),
                DownloadProvider("mediafire_live", "live", lambda source: source.site == "MEDIAFIRE", download_mediafire_live),
                DownloadProvider("saint_live", "live", lambda source: source.site == "SAINT", download_saint_live),
                DownloadProvider("coomer_live", "live", lambda source: source.site == "COOMER", download_coomer_live),
                DownloadProvider("mega_live", "live", lambda source: source.site == "MEGA", download_unsupported_live),
                DownloadProvider("ytdl_live", "live", lambda source: source.site == "YTDL", download_unsupported_live),
            ]
        )

    providers.extend(
        [
            DownloadProvider("gofile_mock", "mock", lambda source: source.site == "GOFILE", download_mock),
            DownloadProvider("bunkr_mock", "mock", lambda source: source.site == "BUNKR", download_mock),
            DownloadProvider("cyberdrop_mock", "mock", lambda source: source.site == "CYBERDROP", download_mock),
            DownloadProvider("cyberfile_mock", "mock", lambda source: source.site == "CYBERFILE", download_mock),
            DownloadProvider("pixeldrain_mock", "mock", lambda source: source.site == "PIXELDRAIN", download_mock),
            DownloadProvider("gd_mock", "mock", lambda source: source.site == "GD", download_mock),
            DownloadProvider("mega_mock", "mock", lambda source: source.site == "MEGA", download_mock),
            DownloadProvider("saint_mock", "mock", lambda source: source.site == "SAINT", download_mock),
            DownloadProvider("coomer_mock", "mock", lambda source: source.site == "COOMER", download_mock),
            DownloadProvider("mediafire_mock", "mock", lambda source: source.site == "MEDIAFIRE", download_mock),
            DownloadProvider("ytdl_mock", "mock", lambda source: source.site == "YTDL", download_mock),
            DownloadProvider("generic_mock", "all", lambda _source: True, download_mock),
        ]
    )
    return providers
