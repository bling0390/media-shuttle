"""Unit tests for the filester parser.

The matcher must accept every ``filester.<tld>`` landing mirror
that aliases the same backend (currently ``.me`` and ``.sh``) so
a future mirror doesn't silently fall through to
``generic_fallback`` (see issues/0007 and 0009). The live
parser hits ``POST /api/public/download`` and reuses the
``cache1.filester.me`` CDN regardless of the landing TLD, so
only the matcher needs testing at this layer.

Run with:
    cd media-shuttle-core && python -m unittest tests.test_filester
"""

from __future__ import annotations

import unittest

from core.providers.parsers_sites.filester import is_filester


class FilesterMatcherTests(unittest.TestCase):
    def test_matches_filester_me(self) -> None:
        self.assertTrue(is_filester("https://filester.me/d/eRtZ6DI"))

    def test_matches_filester_sh(self) -> None:
        # .sh is the mirror that survives the most regional blocks.
        # A user-supplied .sh link must not fall through to
        # generic_fallback (issue #0009).
        self.assertTrue(is_filester("https://filester.sh/d/eRtZ6DI"))

    def test_matches_subdomain_of_filester_sh(self) -> None:
        # Subdomains (``cache1.filester.me``, etc.) count too, in
        # case a future mirror points at a custom subdomain.
        self.assertTrue(is_filester("https://www.filester.sh/d/abc"))

    def test_rejects_other_sites(self) -> None:
        # Cyberdrop shares the same ``/d/<id>`` path shape, so
        # the negative test is the one that protects us.
        self.assertFalse(is_filester("https://cyberdrop.me/d/abc"))
        self.assertFalse(is_filester("https://example.com/d/abc"))
        self.assertFalse(is_filester("https://filester.example.com/d/abc"))

    def test_rejects_empty(self) -> None:
        self.assertFalse(is_filester(""))
        self.assertFalse(is_filester("not-a-url"))


if __name__ == "__main__":
    unittest.main()
