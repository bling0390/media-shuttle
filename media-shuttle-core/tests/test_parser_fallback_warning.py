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
        registry = ParserRegistry(mode="live")
        registry.register_provider(
            ParseProvider("gofile_live", "live", _match_always, _empty)
        )
        registry.register_provider(
            ParseProvider("generic_fallback", "all", _match_always, _generic_ok)
        )

        with self.assertLogs("media-shuttle-core", level="WARNING") as cm:
            sources = registry.parse("https://example.com/whatever")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].site, "GENERIC")
        joined = "\n".join(cm.output)
        self.assertIn("fell through to generic_fallback", joined)
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
