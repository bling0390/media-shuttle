from __future__ import annotations

import os
from typing import Iterable

from ..models import ParsedSource
from ..providers.loader import load_extra_providers
from ..providers.parsers_builtin import builtin_parse_providers
from ..providers.types import ParseProvider


class ParserRegistry:
    def __init__(self, mode: str = "mock") -> None:
        self.mode = mode
        self._providers: list[ParseProvider] = []

    def register_provider(self, provider: ParseProvider) -> None:
        self._providers.append(provider)

    def register(self, matcher, parser, mode: str = "all", name: str = "anonymous") -> None:
        # High-priority registration for runtime overrides.
        self._providers.insert(0, ParseProvider(name=name, mode=mode, matcher=matcher, parser=parser))

    def _iter_active(self) -> Iterable[ParseProvider]:
        for provider in self._providers:
            if provider.mode in ("all", self.mode):
                yield provider

    def parse(self, url: str) -> list[ParsedSource]:
        for provider in self._iter_active():
            if provider.matcher(url):
                result = provider.parser(url)
                if result:
                    return result
        return []


def default_registry(
    mode: str | None = None,
    extra_providers: list[ParseProvider] | None = None,
    extra_provider_modules: list[str] | None = None,
) -> ParserRegistry:
    resolved_mode = (mode or os.getenv("MEDIA_SHUTTLE_IO_MODE", "mock")).lower()
    registry = ParserRegistry(mode=resolved_mode)

    for provider in extra_providers or []:
        registry.register_provider(provider)

    for provider in load_extra_providers("parse", resolved_mode, modules=extra_provider_modules):
        registry.register_provider(provider)

    for provider in builtin_parse_providers(resolved_mode):
        registry.register_provider(provider)

    return registry
