"""Unit tests for the silent-fallback safety log in ParserRegistry.parse.

The ``generic_fallback`` provider matches every URL and returns a
single source pointing at the page URL. Without a safety log, a
site-specific provider that returns ``[]`` (file id missing, API
5xx, etc.) silently falls through to generic_fallback and the worker
uploads whatever the upstream returned — an error HTML body, a
76-byte NXDOMAIN page, etc. — with no indication that something is
wrong. We log a WARNING naming the providers that were tried first
and returned empty. See docs/issues/0005 and 0007 for context.
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from core.plugins.parsers import ParserRegistry
from core.providers.types import ParseProvider


def _match_always(_url: str) -> bool:
    return True


def _empty(_url: str) -> list:
    return []


def _ok(url: str) -> list:
    return [
        type("Source", (), {
            "site": "TEST",
            "page_url": url,
            "download_url": url,
            "file_name": "x",
            "remote_folder": "x",
            "metadata": {},
        })()
    ]


def _generic_ok(url: str) -> list:
    return [
        type("Source", (), {
            "site": "GENERIC",
            "page_url": url,
            "download_url": url,
            "file_name": "x",
            "remote_folder": "x",
            "metadata": {},
        })()
    ]


class ParserRegistryFallbackWarningTests(unittest.TestCase):
    def test_warning_logged_when_specific_provider_returns_empty_then_generic_fallback_refused(self) -> None:
        # As of the no-generic-fallback change, when a
        # site-specific provider matches and returns an
        # empty list we *no longer* hand off to
        # ``generic_fallback``: the page-URL fallback
        # would download the HTML wrapper and upload a
        # 51-byte meta-dump file. We return ``[]``
        # instead, log a WARNING naming the providers
        # that were tried, and let the caller mark the
        # task FAILED with a meaningful reason.
        registry = ParserRegistry(mode="live")
        registry.register_provider(
            ParseProvider("gofile_live", "live", _match_always, _empty)
        )
        registry.register_provider(
            ParseProvider("generic_fallback", "all", _match_always, _generic_ok)
        )

        with self.assertLogs("media-shuttle-core", level="WARNING") as cm:
            sources = registry.parse("https://example.com/whatever")
        self.assertEqual(sources, [])
        joined = "\n".join(cm.output)
        self.assertIn("refusing to use generic_fallback", joined)
        self.assertIn("gofile_live", joined)
        self.assertIn("https://example.com/whatever", joined)

    def test_no_warning_when_first_specific_provider_succeeds(self) -> None:
        registry = ParserRegistry(mode="live")
        registry.register_provider(
            ParseProvider("gofile_live", "live", _match_always, _ok)
        )
        registry.register_provider(
            ParseProvider("generic_fallback", "all", _match_always, _generic_ok)
        )

        # assertNoLogs to make sure we don't emit a misleading warning
        # when a real parser wins.
        with self.assertNoLogs("media-shuttle-core", level="WARNING"):
            sources = registry.parse("https://example.com/whatever")
        self.assertEqual(sources[0].site, "TEST")

    def test_no_warning_when_no_provider_matches_at_all(self) -> None:
        registry = ParserRegistry(mode="live")

        def _no_match(_url: str) -> bool:
            return False

        registry.register_provider(
            ParseProvider("nothing_matches", "live", _no_match, _empty)
        )
        with self.assertNoLogs("media-shuttle-core", level="WARNING"):
            sources = registry.parse("https://example.com/whatever")
        self.assertEqual(sources, [])

    def test_warning_even_when_generic_fallback_is_the_only_provider(self) -> None:
        # As of the no-generic-fallback change we always
        # refuse to use ``generic_fallback``, even when
        # it's the *only* provider. Previously the
        # single-provider path returned the generic
        # result silently; now it returns ``[]`` and
        # logs the same "refusing to use" warning. The
        # operator must wire a real parser to support
        # an unmodelled host.
        registry = ParserRegistry(mode="live")
        registry.register_provider(
            ParseProvider("generic_fallback", "all", _match_always, _generic_ok)
        )
        with self.assertLogs("media-shuttle-core", level="WARNING") as cm:
            sources = registry.parse("https://example.com/whatever")
        self.assertEqual(sources, [])
        joined = "\n".join(cm.output)
        self.assertIn("refusing to use generic_fallback", joined)

    def test_specific_returns_empty_no_longer_falls_through(self) -> None:
        # Regression: a bunkr URL pointing at a typo'd
        # domain (``bunkrr.su``) used to be matched by
        # ``bunkr_live`` (returns []), then silently
        # fall through to ``generic_fallback`` which
        # uploaded the page URL itself as a 51-byte
        # meta-dump. We now bail out instead.
        registry = ParserRegistry(mode="live")
        registry.register_provider(
            ParseProvider("bunkr_live", "live", _match_always, _empty)
        )
        registry.register_provider(
            ParseProvider("generic_fallback", "all", _match_always, _generic_ok)
        )
        sources = registry.parse("https://bunkrr.su/v/sa2dshLAApy4w")
        self.assertEqual(sources, [])

    def test_generic_fallback_never_used(self) -> None:
        # Generic is now strictly an internal "site-specific
        # is the only path" exception — it never wins, ever.
        # Even when *no* site-specific matcher accepts the
        # URL (e.g. a typo'd phishing domain like
        # ``bunkrr.su`` whose own matcher now rejects, or
        # any domain we don't model), we don't fall through
        # to ``generic_fallback`` and silently upload the
        # 51-byte HTML stub the upstream returned. The
        # task is marked FAILED instead. To support an
        # unknown host the operator must wire a real
        # parser.
        registry = ParserRegistry(mode="live")

        def _no_match(_url: str) -> bool:
            return False

        registry.register_provider(
            ParseProvider("bunkr_live", "live", _no_match, _empty)
        )
        registry.register_provider(
            ParseProvider("generic_fallback", "all", _match_always, _generic_ok)
        )
        sources = registry.parse("https://cdn.example.com/video.mp4")
        self.assertEqual(sources, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
