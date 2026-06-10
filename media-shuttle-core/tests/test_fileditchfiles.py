"""Unit tests for the fileditchfiles parser.

The matcher must accept any ``fileditchfiles.<tld>`` landing host
(currently ``.me``) and the parser must extract the signed CDN
URL from the page body's ``<source>`` / ``Download`` button.
HTTP is mocked; the tests pin the documented behaviour so a
future page rewrite is caught immediately.

Run with:
    cd media-shuttle-core && python -m unittest tests.test_fileditchfiles
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.providers.parsers_sites.fileditchfiles import (
    is_fileditchfiles,
    parse_fileditchfiles_live,
)


class FileditchfilesMatcherTests(unittest.TestCase):
    def test_matches_fileditchfiles_me(self) -> None:
        self.assertTrue(is_fileditchfiles("https://fileditchfiles.me/alpha6/abc/file.mp4"))

    def test_matches_subdomain(self) -> None:
        self.assertTrue(is_fileditchfiles("https://www.fileditchfiles.me/alpha6/abc/file.mp4"))

    def test_rejects_other_sites(self) -> None:
        # Filester shares the same ``/d/<id>`` path shape; a
        # negative test pins that we don't cross-match.
        self.assertFalse(is_fileditchfiles("https://fileditch.com/alpha6/abc/file.mp4"))
        self.assertFalse(is_fileditchfiles("https://example.com/alpha6/abc/file.mp4"))
        self.assertFalse(is_fileditchfiles("https://filester.me/alpha6/abc/file.mp4"))

    def test_rejects_empty(self) -> None:
        self.assertFalse(is_fileditchfiles(""))
        self.assertFalse(is_fileditchfiles("not-a-url"))


class FileditchfilesLiveParserTests(unittest.TestCase):
    SAMPLE_HTML = """<!doctype html>
<html><body>
<video controls>
  <source src="https://donotsharethesetemplinksyouidiot.st/alpha6/eb901716274064c926c3/brammed.26.06.03.scarlet.chase.4k.mp4?md5=aFOgiMM_9RQ_2FjowX97ag&expires=1781159792" type="video/mp4">
</video>
<a href="https://donotsharethesetemplinksyouidiot.st/alpha6/eb901716274064c926c3/brammed.26.06.03.scarlet.chase.4k.mp4?md5=aFOgiMM_9RQ_2FjowX97ag&expires=1781159792" class="btn btn-main" id="d1bdfb225664c67" download>⬇ Download</a>
</body></html>"""

    def test_parse_live_promotes_cdn_url_and_filename(self) -> None:
        url = "https://fileditchfiles.me/alpha6/eb901716274064c926c3/brammed.26.06.03.scarlet.chase.4k.mp4"
        with patch("core.providers.parsers_sites.fileditchfiles.http_text", return_value=self.SAMPLE_HTML) as mock_http:
            sources = parse_fileditchfiles_live(url)
        mock_http.assert_called_once()
        self.assertEqual(len(sources), 1)
        src = sources[0]
        # ``page_url`` stays on the landing host (the CDN needs
        # the landing Referer to let the request through).
        self.assertEqual(src.page_url, url)
        # ``download_url`` is the signed CDN URL with the
        # ``&amp;`` decoded back to ``&``.
        self.assertTrue(
            src.download_url.startswith(
                "https://donotsharethesetemplinksyouidiot.st/alpha6/eb901716274064c926c3/"
            )
        )
        self.assertIn("md5=aFOgiMM_9RQ_2FjowX97ag", src.download_url)
        self.assertIn("expires=1781159792", src.download_url)
        # Real filename lifted from the <source src=...> path.
        self.assertEqual(src.file_name, "brammed.26.06.03.scarlet.chase.4k.mp4")
        # Token is the second path segment.
        self.assertEqual(src.remote_folder, "eb901716274064c926c3")
        self.assertTrue(src.metadata["resolved_live"])


if __name__ == "__main__":
    unittest.main()
