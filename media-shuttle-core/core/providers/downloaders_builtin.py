from __future__ import annotations

from ..enums import SourceSite
from .downloaders_sites import (
    download_bunkr_live,
    download_coomer_live,
    download_cyberdrop_live,
    download_cyberfile_live,
    download_filester_live,
    download_gd_live,
    download_gofile_live,
    download_live_generic,
    download_mediafire_live,
    download_mega_live,
    download_mock,
    download_pixeldrain_live,
    download_saint_live,
    download_transfer_live,
    download_turbo_live,
    download_unsupported_live,
    http_download as _http_download,
    write_mock_file as _write_mock_file,
)
from .types import DownloadProvider


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
                    "filester_live",
                    "live",
                    lambda source: source.site == SourceSite.FILESTER.value,
                    download_filester_live,
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
                    "transfer_live",
                    "live",
                    lambda source: source.site == SourceSite.TRANSFERIT.value,
                    download_transfer_live,
                ),
                DownloadProvider(
                    "turbo_live",
                    "live",
                    lambda source: source.site == SourceSite.TURBO.value,
                    download_turbo_live,
                ),
                DownloadProvider(
                    "coomer_live", "live", lambda source: source.site == SourceSite.COOMER.value, download_coomer_live
                ),
                DownloadProvider("mega_live", "live", lambda source: source.site == SourceSite.MEGA.value, download_mega_live),
                DownloadProvider(
                    "ytdl_live", "live", lambda source: source.site == SourceSite.YTDL.value, download_unsupported_live
                ),
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
                "filester_mock", "mock", lambda source: source.site == SourceSite.FILESTER.value, download_mock
            ),
            DownloadProvider(
                "pixeldrain_mock", "mock", lambda source: source.site == SourceSite.PIXELDRAIN.value, download_mock
            ),
            DownloadProvider("gd_mock", "mock", lambda source: source.site == SourceSite.GD.value, download_mock),
            DownloadProvider("mega_mock", "mock", lambda source: source.site == SourceSite.MEGA.value, download_mock),
            DownloadProvider("saint_mock", "mock", lambda source: source.site == SourceSite.SAINT.value, download_mock),
            DownloadProvider(
                "transfer_mock", "mock", lambda source: source.site == SourceSite.TRANSFERIT.value, download_mock
            ),
            DownloadProvider("turbo_mock", "mock", lambda source: source.site == SourceSite.TURBO.value, download_mock),
            DownloadProvider("coomer_mock", "mock", lambda source: source.site == SourceSite.COOMER.value, download_mock),
            DownloadProvider(
                "mediafire_mock", "mock", lambda source: source.site == SourceSite.MEDIAFIRE.value, download_mock
            ),
            DownloadProvider("ytdl_mock", "mock", lambda source: source.site == SourceSite.YTDL.value, download_mock),
            DownloadProvider("generic_mock", "all", lambda _source: True, download_mock),
        ]
    )
    return providers
