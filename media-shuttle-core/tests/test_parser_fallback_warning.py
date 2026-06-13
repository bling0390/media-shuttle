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
    def test_warning_logged_when_specific_provider_returns_empty_then_generic_fallback_takes_over(self) -> None:
        # As of the bunkr typo fix, when a site-specific
        # provider matches and returns an empty list we
        # *no longer* hand off to ``generic_fallback``:
        # the page-URL fallback would download the HTML
        # wrapper and upload a 51-byte meta-dump file.
        # We return ``[]`` instead, log a WARNING naming
        # the providers that were tried, and let the
        # caller mark the task FAILED with a meaningful
        # reason. ``generic_fallback`` only takes over
        # when no specific provider matched at all.
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
        self.assertIn("abandoned after site-specific", joined)
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

    def test_no_warning_when_first_provider_is_already_generic_fallback(self) -> None:
        # If the only provider is generic_fallback (no specific parser
        # to skip), the warning is noisy and unhelpful; we should stay
        # quiet.
        registry = ParserRegistry(mode="live")
        registry.register_provider(
            ParseProvider("generic_fallback", "all", _match_always, _generic_ok)
        )
        with self.assertNoLogs("media-shuttle-core", level="WARNING"):
            sources = registry.parse("https://example.com/whatever")
        self.assertEqual(sources[0].site, "GENERIC")

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

    def test_generic_fallback_still_works_when_no_specific_matched(self) -> None:
        # When a URL doesn't match any specific provider
        # the generic_fallback is still the safety net
        # (e.g. a CDN that has a ``.mp4`` direct file
        # and never had a site-specific parser). This
        # path stays unchanged.
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
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "GENERIC")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
