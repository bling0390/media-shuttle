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

        Operators have reported that the same hazard shows up
        for URLs that **no** site-specific matcher recognises
        at all (e.g. a typo'd phishing domain like
        ``bunkrr.su`` whose own matcher we now reject, or a
        forum-posted link to a domain we don't model). Falling
        through to ``generic_fallback`` in that case is even
        worse than the matched-and-empty case: we don't even
        know what the page is, so we'd happily upload whatever
        bytes the upstream returned (typically a 51-byte HTML
        stub, which has been showing up in the destination
        drive as a row of corrupt files named after the page
        slug). Generic is now strictly an internal
        "site-specific is the only path" exception — it never
        wins, ever. To reach a generic page URL the operator
        must wire a real parser; in the meantime the task is
        marked FAILED.
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
                # We never actually use this — see the
                # outer note — but log it for operators
                # who have to debug a "where did this
                # 79-byte file come from?" incident.
                _logger.warning(
                    "parser_registry.parse refusing to use "
                    f"generic_fallback result url={url!r} "
                    f"tried_empty={tried_empty!r}"
                )
                return []
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
