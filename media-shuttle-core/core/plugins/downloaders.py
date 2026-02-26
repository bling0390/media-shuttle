from __future__ import annotations

import os
from typing import Iterable

from ..models import DownloadResult, ParsedSource
from ..providers.downloaders_builtin import builtin_download_providers
from ..providers.loader import load_extra_providers
from ..providers.types import DownloadProvider


class DownloaderRegistry:
    def __init__(self, mode: str = "mock") -> None:
        self.mode = mode
        self._providers: list[DownloadProvider] = []

    def register_provider(self, provider: DownloadProvider) -> None:
        self._providers.append(provider)

    def register(self, matcher, downloader, mode: str = "all", name: str = "anonymous") -> None:
        # High-priority registration for runtime overrides.
        self._providers.insert(0, DownloadProvider(name=name, mode=mode, matcher=matcher, downloader=downloader))

    def _iter_active(self) -> Iterable[DownloadProvider]:
        for provider in self._providers:
            if provider.mode in ("all", self.mode):
                yield provider

    def download(self, source: ParsedSource) -> DownloadResult:
        for provider in self._iter_active():
            if provider.matcher(source):
                return provider.downloader(source)
        raise RuntimeError(f"no downloader provider matched site: {source.site}")


def default_registry(
    mode: str | None = None,
    extra_providers: list[DownloadProvider] | None = None,
    extra_provider_modules: list[str] | None = None,
) -> DownloaderRegistry:
    resolved_mode = (mode or os.getenv("MEDIA_SHUTTLE_IO_MODE", "mock")).lower()
    registry = DownloaderRegistry(mode=resolved_mode)

    for provider in extra_providers or []:
        registry.register_provider(provider)

    for provider in load_extra_providers("download", resolved_mode, modules=extra_provider_modules):
        registry.register_provider(provider)

    for provider in builtin_download_providers(resolved_mode):
        registry.register_provider(provider)

    return registry
