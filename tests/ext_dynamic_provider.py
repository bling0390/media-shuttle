from __future__ import annotations

from core.models import DownloadResult, ParsedSource, UploadResult
from core.providers.types import DownloadProvider, ParseProvider, UploadProvider


def get_parse_providers(mode: str):
    return [
        ParseProvider(
            name="module-parse",
            mode="all",
            matcher=lambda url: "module.loaded" in url,
            parser=lambda url: [
                ParsedSource(
                    site="MODULE_PARSE",
                    page_url=url,
                    download_url=url,
                    file_name="module.bin",
                )
            ],
        )
    ]


def get_download_providers(mode: str):
    return [
        DownloadProvider(
            name="module-download",
            mode="all",
            matcher=lambda source: source.site == "MODULE_PARSE",
            downloader=lambda source: DownloadResult(
                site=source.site,
                source_url=source.download_url,
                local_path="/tmp/module-downloaded.bin",
                size_bytes=7,
                file_name="module.bin",
                remote_folder=None,
            ),
        )
    ]


def get_upload_providers(mode: str):
    return [
        UploadProvider(
            name="module-upload",
            mode="all",
            matcher=lambda target: target == "MODULE_TARGET",
            uploader=lambda download, destination: UploadResult(location=f"module://{destination}/{download.file_name}"),
        )
    ]
