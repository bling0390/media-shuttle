from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ...models import DownloadResult, ParsedSource
from ...utils import cleanup_local_download
from ..user_agents import with_random_user_agent


def download_dir() -> Path:
    path = Path(os.getenv("MEDIA_SHUTTLE_DOWNLOAD_DIR", "/tmp/media-shuttle"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def materialize_path(source: ParsedSource) -> Path:
    # Use temporary local name to avoid extremely long original filenames.
    seed = hashlib.sha1(source.download_url.encode("utf-8")).hexdigest()[:16]
    task_dir = download_dir() / seed
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir / "tmp.part"


def write_mock_file(path: Path, source: ParsedSource) -> int:
    payload = f"site={source.site};url={source.download_url}\n".encode("utf-8")
    path.write_bytes(payload)
    return len(payload)


def http_download(url: str, path: Path, headers: dict[str, str] | None = None) -> int:
    response = httpx.request(
        method="GET",
        url=url,
        headers=with_random_user_agent(headers),
        timeout=60.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    data = response.content
    path.write_bytes(data)
    return len(data)


def build_result(source: ParsedSource, output: Path, size: int, source_url: str | None = None) -> DownloadResult:
    return DownloadResult(
        site=source.site,
        source_url=source_url or source.download_url,
        local_path=str(output),
        size_bytes=size,
        file_name=source.file_name,
        remote_folder=source.remote_folder,
    )


def download_mock(source: ParsedSource) -> DownloadResult:
    output = materialize_path(source)
    try:
        return build_result(source, output, write_mock_file(output, source))
    except Exception:
        cleanup_local_download(str(output))
        raise


def download_live_generic(
    source: ParsedSource,
    headers: dict[str, str] | None = None,
    actual_url: str | None = None,
) -> DownloadResult:
    url = actual_url or source.download_url
    output = materialize_path(source)
    try:
        return build_result(source, output, http_download(url, output, headers=headers), source_url=url)
    except Exception:
        cleanup_local_download(str(output))
        raise


def is_direct_file_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return bool(re.search(r"\.(mp4|mov|mkv|avi|webm|jpg|jpeg|png|gif|zip|rar|7z|tar|gz|pdf|mp3|m4a)(?:$|/)", path))
