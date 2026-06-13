from __future__ import annotations

import logging
import os
from typing import Iterable

from ..models import ParsedSource
from ..providers.loader import load_extra_providers
from ..providers.parsers_builtin import builtin_parse_providers
from ..providers.types import ParseProvider

_logger = logging.getLogger("media-shuttle-core")


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
        """Run each active provider in order; first non-empty result wins.

        The ``generic_fallback`` provider matches every URL and returns
        a single source with the page URL as the download URL, so a
        site-specific provider returning ``[]`` (file id missing, API
        5xx, etc.) silently falls through and the worker uploads
        whatever the upstream returned — an error HTML body, a 76-byte
        NXDOMAIN page, the raw login redirect, etc. To make that
        visible, we log a WARNING whenever ``generic_fallback`` is the
        provider that produced the result, naming the providers that
        were tried first and returned empty. See docs/issues/0005 and
        docs/issues/0007 for context.

        As of this change we also **stop** the fall-through when
        at least one site-specific provider matched and returned an
        empty list. Reason: a non-empty result from ``generic_fallback``
        in that situation is a download of the page URL itself, which
        is almost never what the operator wants (a bunkr page, a
        phishing replica at a typo'd domain, a CDN that just returns
        the HTML wrapper, etc.). Returning ``[]`` surfaces the
        failure as ``parser returned no sources`` and the task is
        marked FAILED by the surrounding logic. The WARNING is
        retained so operators can see what was tried.
        """
        tried_empty: list[str] = []
        matched_specific = False
        for provider in self._iter_active():
            if not provider.matcher(url):
                continue
            is_generic = provider.name == "generic_fallback"
            if not is_generic:
                matched_specific = True
            result = provider.parser(url)
            if result:
                # The site-specific provider succeeded.
                # Nothing more to do.
                if not is_generic:
                    return result
                # ``generic_fallback`` produced a result.
                # If a site-specific provider was tried
                # and returned empty, suppress the
                # fall-through: returning the page URL
                # itself is almost always wrong (it
                # becomes a 51-byte meta-dump uploaded
                # to the destination drive).
                if matched_specific and tried_empty:
                    _logger.warning(
                        "parser_registry.parse abandoned after "
                        f"site-specific providers returned empty "
                        f"url={url!r} tried_empty={tried_empty!r}"
                    )
                    return []
                # No site-specific provider matched at
                # all — this is a true fallback case
                # (e.g. a direct file URL or an unknown
                # CDN). Keep the legacy WARNING so
                # operators can see the no-match.
                if tried_empty:
                    _logger.warning(
                        "parser_registry.parse fell through to "
                        f"generic_fallback url={url!r} "
                        f"tried_empty={tried_empty!r}"
                    )
                return result
            tried_empty.append(provider.name)
        if matched_specific and tried_empty:
            _logger.warning(
                "parser_registry.parse abandoned after site-specific "
                f"providers returned empty url={url!r} tried_empty={tried_empty!r}"
            )
            return []
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
