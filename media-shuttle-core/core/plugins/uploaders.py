from __future__ import annotations

import os
from typing import Iterable

from ..models import DownloadResult, UploadResult
from ..providers.loader import load_extra_providers
from ..providers.types import UploadProvider
from ..providers.uploaders_builtin import builtin_upload_providers


class UploaderRegistry:
    def __init__(self, mode: str = "mock") -> None:
        self.mode = mode
        self._providers: list[UploadProvider] = []

    def register_provider(self, provider: UploadProvider) -> None:
        self._providers.append(provider)

    def register(self, matcher, uploader, mode: str = "all", name: str = "anonymous") -> None:
        # High-priority registration for runtime overrides.
        self._providers.insert(0, UploadProvider(name=name, mode=mode, matcher=matcher, uploader=uploader))

    def _iter_active(self) -> Iterable[UploadProvider]:
        for provider in self._providers:
            if provider.mode in ("all", self.mode):
                yield provider

    def upload(self, target: str, download: DownloadResult, destination: str) -> UploadResult:
        for provider in self._iter_active():
            if provider.matcher(target):
                return provider.uploader(download, destination)
        raise ValueError(f"unsupported target: {target}")


def default_registry(
    mode: str | None = None,
    extra_providers: list[UploadProvider] | None = None,
    extra_provider_modules: list[str] | None = None,
) -> UploaderRegistry:
    resolved_mode = (mode or os.getenv("MEDIA_SHUTTLE_IO_MODE", "mock")).lower()
    registry = UploaderRegistry(mode=resolved_mode)

    for provider in extra_providers or []:
        registry.register_provider(provider)

    for provider in load_extra_providers("upload", resolved_mode, modules=extra_provider_modules):
        registry.register_provider(provider)

    for provider in builtin_upload_providers(resolved_mode):
        registry.register_provider(provider)

    return registry
